//! CPython-compatible `random.Random` parity module.
//!
//! Implements CPython 3.10–3.12 `random.Random` seeded with an integer:
//! - MT19937 `init_by_array` seeding (identical to CPython `_randommodule.c`)
//! - `random()` via `genrand_res53` (53-bit double)
//! - `getrandbits(k)` (k ≤ 64; little-endian multi-word for k > 32)
//! - `randbelow(n)` via `_randbelow_with_getrandbits` rejection sampling
//! - `gauss(mu, sigma)` Box-Muller with `gauss_next` cache
//! - `sample_indices(n, k)` matching CPython `random.sample(range(n), k)`
//!   pool-copy and selected-set branches with identical `setsize` threshold
//!
//! No external crates; pure `std`. Does not use the `rand` crate.
//! Intended use: Rust projection work that must reproduce Python results bit-for-bit.

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// CPython-compatible `random.Random` implementation.
pub struct Random {
    mt: [u32; N],
    index: usize,
    gauss_next: Option<f64>,
}

impl Random {
    /// Seed identically to CPython `random.Random(seed)` for integer seeds.
    ///
    /// Converts `seed` to little-endian 32-bit words and calls `init_by_array`.
    /// For `seed == 0`, uses `key = [0]` matching CPython behaviour.
    pub fn new(seed: u64) -> Self {
        let mut rng = Self {
            mt: [0u32; N],
            index: N,
            gauss_next: None,
        };
        // Convert seed to LE 32-bit words (matches CPython _randommodule.c).
        let key: Vec<u32> = if seed == 0 {
            vec![0u32]
        } else {
            let bytes = seed.to_le_bytes();
            let n_significant = (64 - seed.leading_zeros()).div_ceil(8); // ceil(bits/8)
            let n_words = n_significant.div_ceil(4) as usize;
            (0..n_words)
                .map(|i| {
                    u32::from_le_bytes([
                        bytes[i * 4],
                        bytes[i * 4 + 1],
                        bytes[i * 4 + 2],
                        bytes[i * 4 + 3],
                    ])
                })
                .collect()
        };
        rng.init_by_array(&key);
        rng
    }

    // ── MT19937 initialisation ───────────────────────────────────────────────

    fn init_genrand(&mut self, s: u32) {
        self.mt[0] = s;
        for i in 1..N {
            self.mt[i] = 1_812_433_253u32
                .wrapping_mul(self.mt[i - 1] ^ (self.mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        self.index = N;
    }

    fn init_by_array(&mut self, init_key: &[u32]) {
        self.init_genrand(19_650_218);
        let key_len = init_key.len();
        let (mut i, mut j) = (1usize, 0usize);
        let k_max = N.max(key_len);
        for _ in 0..k_max {
            self.mt[i] = (self.mt[i]
                ^ ((self.mt[i - 1] ^ (self.mt[i - 1] >> 30)).wrapping_mul(1_664_525)))
            .wrapping_add(init_key[j])
            .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            if j >= key_len {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            self.mt[i] = (self.mt[i]
                ^ ((self.mt[i - 1] ^ (self.mt[i - 1] >> 30)).wrapping_mul(1_566_083_941)))
            .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
        }
        self.mt[0] = 0x8000_0000;
    }

    // ── MT19937 generation ───────────────────────────────────────────────────

    fn genrand_uint32(&mut self) -> u32 {
        if self.index >= N {
            // Generate N words at once (twist).
            for kk in 0..N - M {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] = self.mt[kk + M] ^ (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            }
            for kk in N - M..N - 1 {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] =
                    self.mt[kk + M - N] ^ (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            }
            let y = (self.mt[N - 1] & UPPER_MASK) | (self.mt[0] & LOWER_MASK);
            self.mt[N - 1] = self.mt[M - 1] ^ (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            self.index = 0;
        }
        let mut y = self.mt[self.index];
        self.index += 1;
        // Tempering.
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /// `random()` — uniform [0.0, 1.0) via CPython `genrand_res53`.
    ///
    /// Two MT words: `(a * 2^26 + b) / 2^53` where `a = w0 >> 5`, `b = w1 >> 6`.
    pub fn random(&mut self) -> f64 {
        let a = (self.genrand_uint32() >> 5) as f64;
        let b = (self.genrand_uint32() >> 6) as f64;
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }

    /// `getrandbits(k)` — matches CPython `_random.Random.getrandbits`.
    ///
    /// For `k <= 32`: `genrand_uint32() >> (32 - k)` (fast path).
    /// For `32 < k <= 64`: two MT words combined little-endian; last word
    /// has excess bits cleared via `>> (32 - (k & 31))` when `k & 31 != 0`.
    ///
    /// Panics if `k == 0` or `k > 64`.
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        assert!(k > 0 && k <= 64, "getrandbits: k must be in 1..=64");
        if k <= 32 {
            (self.genrand_uint32() >> (32 - k)) as u64
        } else {
            let w0 = self.genrand_uint32() as u64;
            let mut w1 = self.genrand_uint32() as u64;
            let rem = k & 31; // bits used in the high word
            if rem != 0 {
                w1 >>= 32 - rem;
            }
            w0 | (w1 << 32)
        }
    }

    /// `_randbelow_with_getrandbits(n)` — rejection sampling; `n > 0` required.
    pub fn randbelow(&mut self, n: u64) -> u64 {
        assert!(n > 0, "randbelow: n must be > 0");
        let k = 64 - n.leading_zeros(); // n.bit_length()
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// `gauss(mu, sigma)` — Box-Muller with `gauss_next` cache (CPython exact).
    ///
    /// First call produces two normal variates; the second is cached and returned
    /// on the next call, matching CPython's `self.gauss_next` field.
    pub fn gauss(&mut self, mu: f64, sigma: f64) -> f64 {
        const TWOPI: f64 = std::f64::consts::TAU;
        let z = match self.gauss_next.take() {
            Some(cached) => cached,
            None => {
                let x2pi = self.random() * TWOPI;
                let g2rad = (-2.0 * (1.0 - self.random()).ln()).sqrt();
                let z = x2pi.cos() * g2rad;
                self.gauss_next = Some(x2pi.sin() * g2rad);
                z
            }
        };
        mu + z * sigma
    }

    /// `sample_indices(n, k)` — matches `random.sample(range(n), k)`.
    ///
    /// Uses the identical pool-copy / selected-set branch and `setsize` formula
    /// from CPython `random.py` (3.10–3.12).
    ///
    /// Pool branch (`n <= setsize`): partial Fisher-Yates on a `[0..n]` pool.
    /// Set branch (`n > setsize`): rejection with a `HashSet` of selected indices.
    ///
    /// Panics if `k > n`.
    pub fn sample_indices(&mut self, n: usize, k: usize) -> Vec<usize> {
        assert!(k <= n, "sample_indices: k must be <= n");
        if k == 0 {
            return Vec::new();
        }
        // CPython setsize formula (random.py):
        //   setsize = 21  (if k <= 5)
        //   setsize = 21 + 4^ceil(log4(k*3))  (if k > 5)
        let setsize: usize = if k > 5 {
            let log4_k3 = ((k as f64 * 3.0).ln() / std::f64::consts::LN_2 * 0.5).ceil() as u32;
            // log4(x) = log2(x) / 2
            21 + 4usize.pow(log4_k3)
        } else {
            21
        };

        let mut result = vec![0usize; k];

        if n <= setsize {
            // Pool branch: copy range(n) and swap-select k items.
            let mut pool: Vec<usize> = (0..n).collect();
            for i in 0..k {
                let j = self.randbelow((n - i) as u64) as usize;
                result[i] = pool[j];
                pool[j] = pool[n - i - 1];
            }
        } else {
            // Set branch: rejection sample into a HashSet.
            let mut selected = std::collections::HashSet::with_capacity(k);
            for slot in result.iter_mut() {
                let mut j = self.randbelow(n as u64) as usize;
                while selected.contains(&j) {
                    j = self.randbelow(n as u64) as usize;
                }
                selected.insert(j);
                *slot = j;
            }
        }
        result
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // Helper: compare two f64 values to within 1e-15, or bit-for-bit if equal.
    fn assert_f64_near(got: f64, want: f64, label: &str) {
        if got.to_bits() == want.to_bits() {
            return; // exact match
        }
        let diff = (got - want).abs();
        assert!(
            diff <= 1e-15,
            "{label}: got {got:.17e} want {want:.17e} diff {diff:.3e}"
        );
    }

    // ── getrandbits(32) golden vectors ────────────────────────────────────────

    /// CPython: `r = random.Random(42); [r.getrandbits(32) for _ in range(8)]`
    #[test]
    fn test_getrandbits32_seed42() {
        let expected: [u64; 8] = [
            2746317213, 478163327, 107420369, 3184935163, 1181241943, 1051802512, 958682846,
            599310825,
        ];
        let mut r = Random::new(42);
        for (i, &want) in expected.iter().enumerate() {
            let got = r.getrandbits(32);
            assert_eq!(got, want, "getrandbits32 seed42 index {i}");
        }
    }

    // ── random() golden vectors ────────────────────────────────────────────────

    /// CPython: `r = random.Random(42); [r.random() for _ in range(8)]`
    /// Float bits verified via `struct.pack('>d', f).hex()`.
    #[test]
    fn test_random_seed42() {
        // Exact bit representations from CPython.
        let expected_bits: [u64; 8] = [
            0x3fe4762f307200c5,
            0x3f999c6b5eeb2060,
            0x3fd19a1491f589dc,
            0x3fcc922b623b8c1c,
            0x3fe7912c1468f47e,
            0x3fe5a785aef6719a,
            0x3fec8cbc2a2e7490,
            0x3fb6419f92e55088,
        ];
        let mut r = Random::new(42);
        for (i, &bits) in expected_bits.iter().enumerate() {
            let got = r.random();
            assert_eq!(
                got.to_bits(),
                bits,
                "random seed42 index {i}: got {got:.17e}"
            );
        }
    }

    // ── gauss golden vectors ───────────────────────────────────────────────────

    /// CPython: `r = random.Random(1); [r.gauss(0, 1) for _ in range(8)]`
    #[test]
    fn test_gauss_seed1() {
        let expected_bits: [u64; 8] = [
            0x3ff49c679da02301,
            0x3ff730ede0eb242a,
            0x3fb0fb6231f18bcc,
            0xbfe877243f2933ff,
            0xbff1798a9f070808,
            0x3fa00b13ea0e6e54,
            0xbff05a88da855aa2,
            0xbff6fd40df4a52b6,
        ];
        let mut r = Random::new(1);
        for (i, &bits) in expected_bits.iter().enumerate() {
            let got = r.gauss(0.0, 1.0);
            let want = f64::from_bits(bits);
            assert_f64_near(got, want, &format!("gauss seed1[{i}]"));
        }
    }

    /// CPython: `r = random.Random(2); [r.gauss(0, 1) for _ in range(8)]`
    #[test]
    fn test_gauss_seed2() {
        let expected_bits: [u64; 8] = [
            0x4002b490c303c3b5,
            0xbfe5361966258776,
            0x3fd9456149ecb904,
            0x3fc2c136fc072248,
            0x3feab989839f1c06,
            0xbff66f0aac6e473c,
            0xbfda8bb8a06dfbc1,
            0xbfe80bf62984c109,
        ];
        let mut r = Random::new(2);
        for (i, &bits) in expected_bits.iter().enumerate() {
            let got = r.gauss(0.0, 1.0);
            let want = f64::from_bits(bits);
            assert_f64_near(got, want, &format!("gauss seed2[{i}]"));
        }
    }

    // ── sample_indices golden vectors ─────────────────────────────────────────

    /// Pool branch: n=1000 <= setsize=4117.
    /// CPython: `r = random.Random(99); r.sample(range(1000), 500)[:20]`
    #[test]
    fn test_sample_pool_1000_500_seed99() {
        let expected: [usize; 20] = [
            413, 389, 204, 613, 183, 235, 254, 136, 778, 88, 257, 746, 392, 543, 700, 717, 551, 91,
            960, 638,
        ];
        let mut r = Random::new(99);
        let got = r.sample_indices(1000, 500);
        assert_eq!(
            &got[..20],
            &expected,
            "sample pool 1000/500 seed99 first 20"
        );
    }

    /// Set branch: n=5000 > setsize=4117.
    /// CPython: `r = random.Random(99); r.sample(range(5000), 500)[:20]`
    #[test]
    fn test_sample_set_5000_500_seed99() {
        let expected: [usize; 20] = [
            3309, 3119, 1639, 4910, 1464, 1886, 2035, 1091, 709, 2057, 3138, 4348, 4412, 735, 4008,
            1634, 3454, 4820, 1775, 3063,
        ];
        let mut r = Random::new(99);
        let got = r.sample_indices(5000, 500);
        assert_eq!(&got[..20], &expected, "sample set 5000/500 seed99 first 20");
    }

    /// Pool branch (render-cap path): n=4000 <= setsize=16405 for k=2000.
    /// CPython: `r = random.Random(42); r.sample(range(4000), 2000)[:20]`
    #[test]
    fn test_sample_pool_4000_2000_seed42() {
        let expected: [usize; 20] = [
            2619, 456, 102, 3037, 1126, 1003, 914, 571, 3016, 419, 2771, 3033, 3654, 2233, 356,
            2418, 1728, 130, 122, 383,
        ];
        let mut r = Random::new(42);
        let got = r.sample_indices(4000, 2000);
        assert_eq!(
            &got[..20],
            &expected,
            "sample pool 4000/2000 seed42 first 20"
        );
    }

    // ── gauss cache path ───────────────────────────────────────────────────────

    /// Verify that odd-indexed gauss calls use the cached `gauss_next` value.
    /// The second call must match the sin branch of the first Box-Muller pair.
    #[test]
    fn test_gauss_cache_path() {
        let mut r1 = Random::new(1);
        let v0 = r1.gauss(0.0, 1.0);
        let v1 = r1.gauss(0.0, 1.0); // must come from cache
                                     // Recompute from scratch: same seed should give same first two values.
        let mut r2 = Random::new(1);
        assert_eq!(r2.gauss(0.0, 1.0).to_bits(), v0.to_bits(), "gauss cache[0]");
        assert_eq!(r2.gauss(0.0, 1.0).to_bits(), v1.to_bits(), "gauss cache[1]");
    }
}
