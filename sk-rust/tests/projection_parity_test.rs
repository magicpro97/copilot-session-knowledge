//! CPython-parity harness for `browse::algo::projection`.
//!
//! All golden values were produced by running `gen_golden.py` against
//! `browse/core/projection.py` on Python 3.12 (Windows).  The script is
//! located at the repo root and is the authoritative source for these
//! constants.
//!
//! ## Coverage
//! - Small dataset (n=10 < PCA_SAMPLE=500): exercises cpyrand gauss init path.
//! - Large dataset (n=600 > PCA_SAMPLE=500): exercises cpyrand sample_indices
//!   (seed 99) PCA sample path.
//! - Render-cap helper (n=2500, max=2000): proves `sample_render_indices`
//!   returns `cpyrand::Random::new(42).sample_indices(n, max)` order.

use sk::browse::algo::{cpyrand, projection};

// ── Tolerance ─────────────────────────────────────────────────────────────────

/// Absolute tolerance for coordinate comparison (Python vs Rust pca_2d).
/// 1e-9 is the spec target; on IEEE 754 Windows platforms we observe exact
/// bit-level agreement, so this headroom handles any future libm drift.
const TOL: f64 = 1e-9;

fn assert_near(got: f64, want: f64, label: &str) {
    let diff = (got - want).abs();
    assert!(
        diff <= TOL,
        "{label}: got={got:.17e} want={want:.17e} diff={diff:.3e} > TOL={TOL:.0e}"
    );
}

// ── Vector generation helper ───────────────────────────────────────────────────

/// Generate an `n × dims` matrix using CPython-identical gauss(0,1) values.
/// Matches `[random.Random(seed).gauss(0,1) for _ in range(n*dims)]` shaped
/// into rows, so Rust input vectors are bit-for-bit identical to Python's.
fn make_vecs_gauss(seed: u64, n: usize, dims: usize) -> Vec<Vec<f64>> {
    let mut rng = cpyrand::Random::new(seed);
    (0..n)
        .map(|_| (0..dims).map(|_| rng.gauss(0.0, 1.0)).collect())
        .collect()
}

// ── Golden constants: small case (n=10, dims=4, input seed=777) ──────────────
//
// Python:
//   rng = random.Random(777)
//   vecs = [[rng.gauss(0,1) for _ in range(4)] for _ in range(10)]
//   xs, ys = pca_2d(vecs)
//
// Bits verified with `struct.pack('>d', v).hex()`.

const SMALL_XS: &[f64] = &[
    f64::from_bits(0xbfd04c044b532beb),
    f64::from_bits(0x4001645940ecfbc2),
    f64::from_bits(0xc008a589ca3e69ca),
    f64::from_bits(0x3fd51d7aaea840e2),
    f64::from_bits(0xbfba8a17bc252796),
    f64::from_bits(0x3fb568f66567b2c6),
    f64::from_bits(0x4001ec87ffe3193c),
    f64::from_bits(0x3fd9b9ae6eabc8b4),
    f64::from_bits(0xbff43738dbae4a5c),
    f64::from_bits(0xbfe0e05a6112d84f),
];

const SMALL_YS: &[f64] = &[
    f64::from_bits(0xbff03375c6687b3a),
    f64::from_bits(0xbfe72ca788ccc9fc),
    f64::from_bits(0xbfcdfc751ff2209e),
    f64::from_bits(0x3ff37086a6ec271d),
    f64::from_bits(0x40046a840ab14d8a),
    f64::from_bits(0xbff0ea985c382ab7),
    f64::from_bits(0xbfcdc83520edc33e),
    f64::from_bits(0x3fd73ca2f81e216a),
    f64::from_bits(0xbfe42bcc24c1598a),
    f64::from_bits(0xbfce8ed1c694b2da),
];

// ── Golden constants: large case (n=600, dims=4, input seed=888) — first 20 ──
//
// Python:
//   rng = random.Random(888)
//   vecs = [[rng.gauss(0,1) for _ in range(4)] for _ in range(600)]
//   xs, ys = pca_2d(vecs)

const LARGE_XS_20: &[f64] = &[
    f64::from_bits(0x3fe75568e12cf286),
    f64::from_bits(0x3fe84f4dbc62465b),
    f64::from_bits(0x3ff56db716dacca8),
    f64::from_bits(0xbfb703dd80a2013d),
    f64::from_bits(0x3fd9348601fca150),
    f64::from_bits(0xbff1e90c041afe01),
    f64::from_bits(0xbffadc94b8636a8f),
    f64::from_bits(0xbfe5e34df485206f),
    f64::from_bits(0x3fae75b86556800b),
    f64::from_bits(0x3ffdc86a7b472045),
    f64::from_bits(0xbff946a77bf3f9f9),
    f64::from_bits(0xbfad86883baf1f68),
    f64::from_bits(0xbfc2d2c7ac34a7b9),
    f64::from_bits(0xbfeb38bfb25753de),
    f64::from_bits(0xbfe85cedde3531dd),
    f64::from_bits(0x4002209e5172d8bf),
    f64::from_bits(0xbfeedbcae9ce2852),
    f64::from_bits(0x3fda40c1ed51561a),
    f64::from_bits(0x3fd67d0636e37cbb),
    f64::from_bits(0x3ff4c883087531cd),
];

const LARGE_YS_20: &[f64] = &[
    f64::from_bits(0x3fe3abd4d24192b8),
    f64::from_bits(0xbfbc469959f9b9ac),
    f64::from_bits(0xbffdc87a0b9e355d),
    f64::from_bits(0x3fe5b7c7a179cd6d),
    f64::from_bits(0xbfc59dc04807b00f),
    f64::from_bits(0xbffd1db49873ca69),
    f64::from_bits(0x3fd7b2a3b616a651),
    f64::from_bits(0xbff125c5874604c5),
    f64::from_bits(0x3fa408a2af334272),
    f64::from_bits(0xbffa414fc009b22e),
    f64::from_bits(0xbff1633e9417becf),
    f64::from_bits(0x3ff4d7f137cbc231),
    f64::from_bits(0x3fe9d3fd4b1037cc),
    f64::from_bits(0x3ff790f140a544f2),
    f64::from_bits(0xbff5f53296fc26d2),
    f64::from_bits(0xbfeabca6ae8ae8bc),
    f64::from_bits(0xbfea1622317ec86e),
    f64::from_bits(0x3ff01955361ef1dc),
    f64::from_bits(0x3fe055c04f6aa84f),
    f64::from_bits(0xbfe2a45ed0435b1a),
];

// ── Golden constants: render-cap indices (n=2500, max=2000, seed=42) — first 20
//
// Python: `random.Random(42).sample(range(2500), 2000)[:20]`
const RENDER_CAP_IDX_20: [usize; 20] = [
    456, 102, 1126, 1003, 914, 571, 419, 2233, 356, 2418, 1728, 130, 122, 383, 895, 952, 2069,
    2465, 108, 2298,
];

// ── Tests ─────────────────────────────────────────────────────────────────────

/// Small dataset (n=10 < PCA_SAMPLE): all rows used as sample, so this test
/// exercises the cpyrand gauss init path for power-iteration eigenvectors.
#[test]
fn projection_parity_small_n10_dims4() {
    let vecs = make_vecs_gauss(777, 10, 4);
    let (xs, ys) = projection::pca_2d(&vecs);

    assert_eq!(xs.len(), 10, "xs length");
    assert_eq!(ys.len(), 10, "ys length");

    for i in 0..10 {
        assert_near(xs[i], SMALL_XS[i], &format!("small xs[{i}]"));
        assert_near(ys[i], SMALL_YS[i], &format!("small ys[{i}]"));
    }
}

/// Large dataset (n=600 > PCA_SAMPLE=500): exercises the cpyrand seed-99
/// `sample_indices` path for PCA sample selection, then verifies the full
/// projection output against Python golden.  Checks first 20 coordinates.
#[test]
fn projection_parity_large_n600_dims4_sample_path() {
    let vecs = make_vecs_gauss(888, 600, 4);
    let (xs, ys) = projection::pca_2d(&vecs);

    assert_eq!(xs.len(), 600, "xs length");
    assert_eq!(ys.len(), 600, "ys length");

    for i in 0..20 {
        assert_near(xs[i], LARGE_XS_20[i], &format!("large xs[{i}]"));
        assert_near(ys[i], LARGE_YS_20[i], &format!("large ys[{i}]"));
    }
}

/// Render-cap helper: verifies `sample_render_indices(2500, 2000)` produces
/// the same first-20 indices as Python's `random.Random(42).sample(range(2500), 2000)`.
#[test]
fn projection_parity_render_cap_indices_n2500_max2000() {
    let got = projection::sample_render_indices(2500, 2000);
    assert_eq!(got.len(), 2000, "render-cap result length");
    assert_eq!(
        &got[..20],
        &RENDER_CAP_IDX_20,
        "render-cap first-20 indices must match Python golden"
    );
}

/// Identity path: when `n <= max_render`, `sample_render_indices` returns 0..n.
#[test]
fn projection_render_cap_identity_when_n_le_max() {
    let got = projection::sample_render_indices(100, 2000);
    let expected: Vec<usize> = (0..100).collect();
    assert_eq!(got, expected, "identity range when n <= max_render");
}
