//! Pure-Rust port of `browse/core/projection.py` — PCA-to-2D projection.
//!
//! No I/O, no cache, no DB.
//!
//! ## CPython parity
//!
//! This module matches Python's `projection.pca_2d` output bit-for-bit on
//! IEEE 754 platforms (verified by the `projection_parity_test` integration
//! test harness):
//!
//! - **Power-iteration init**: uses `cpyrand::Random::new(seed).gauss(0.0, 1.0)`
//!   for each dimension (seed 1 for e1, seed 2 for e2 on the deflated sample),
//!   matching Python's `random.Random(seed).gauss(0, 1)`.
//!
//! - **PCA sample selection**: when `n > PCA_SAMPLE`, uses
//!   `cpyrand::Random::new(99).sample_indices(n, PCA_SAMPLE)` preserving the
//!   CPython `random.sample(range(n), PCA_SAMPLE)` order.
//!
//! - **Render-cap**: `sample_render_indices(n, max_render)` uses
//!   `cpyrand::Random::new(42).sample_indices(n, max_render)` when
//!   `n > max_render`, matching Python's `random.Random(42).sample(raw, MAX_RENDER)`.

/// Number of rows sampled from the full data to compute eigenvectors.
pub const PCA_SAMPLE: usize = 500;
/// Power-iteration count used when finding each eigenvector.
pub const POWER_ITERS: usize = 50;

// ── low-level vector helpers ─────────────────────────────────────────────────

/// Decode a little-endian f32 BLOB into a `Vec<f32>`.
///
/// Returns `None` when `blob` is shorter than `n_dims * 4` bytes.
pub fn decode_vector_le_f32(blob: &[u8], n_dims: usize) -> Option<Vec<f32>> {
    if blob.len() < n_dims * 4 {
        return None;
    }
    let mut out = Vec::with_capacity(n_dims);
    for i in 0..n_dims {
        let b = [
            blob[i * 4],
            blob[i * 4 + 1],
            blob[i * 4 + 2],
            blob[i * 4 + 3],
        ];
        out.push(f32::from_le_bytes(b));
    }
    Some(out)
}

/// Dot product of two equal-length slices.
pub fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Euclidean norm of a vector.
pub fn norm(v: &[f64]) -> f64 {
    v.iter().map(|x| x * x).sum::<f64>().sqrt()
}

/// Normalise `v`; returns the original vec unchanged when norm < 1e-12.
pub fn normalize(v: &[f64]) -> Vec<f64> {
    let n = norm(v);
    if n < 1e-12 {
        return v.to_vec();
    }
    let inv = 1.0 / n;
    v.iter().map(|x| x * inv).collect()
}

/// Matrix–vector product: `X @ v`  (X is a row-major slice).
pub fn mat_vec(x: &[Vec<f64>], v: &[f64]) -> Vec<f64> {
    x.iter().map(|row| dot(row, v)).collect()
}

/// Transposed matrix–vector product: `X^T @ w`.
pub fn mat_t_vec(x: &[Vec<f64>], w: &[f64]) -> Vec<f64> {
    if x.is_empty() {
        return vec![];
    }
    let n_dims = x[0].len();
    let mut result = vec![0.0_f64; n_dims];
    for (i, row) in x.iter().enumerate() {
        let wi = w[i];
        if wi == 0.0 {
            continue;
        }
        for (j, &xij) in row.iter().enumerate() {
            result[j] += wi * xij;
        }
    }
    result
}

/// Remove the component along unit vector `e` from every row of `X`.
pub fn deflate(x: &[Vec<f64>], e: &[f64]) -> Vec<Vec<f64>> {
    x.iter()
        .map(|row| {
            let d = dot(row, e);
            row.iter()
                .zip(e.iter())
                .map(|(xi, ei)| xi - d * ei)
                .collect()
        })
        .collect()
}

/// Dominant eigenvector of `X^T X` via power iteration.
///
/// `init` is the starting vector (caller supplies a deterministic value).
/// Returns an empty vec when `X` is empty or all-zero.
pub fn power_iter(x: &[Vec<f64>], n_iters: usize, init: &[f64]) -> Vec<f64> {
    if x.is_empty() || x[0].is_empty() {
        return vec![];
    }
    let n_dims = x[0].len();
    // Normalise the supplied init; fall back to the first standard basis vector
    // if init normalises to zero (e.g. after deflation killed the component).
    let mut v = normalize(init);
    if norm(&v) < 1e-12 {
        let mut basis = vec![0.0_f64; n_dims];
        if n_dims > 0 {
            basis[0] = 1.0;
        }
        v = basis;
    }
    for _ in 0..n_iters {
        let w = mat_vec(x, &v);
        let u = mat_t_vec(x, &w);
        let vn = normalize(&u);
        if norm(&vn) < 1e-12 {
            break;
        }
        v = vn;
    }
    v
}

// ── PCA 2D ───────────────────────────────────────────────────────────────────

/// Project *vectors* (equal-length rows) to 2D via two-component PCA.
///
/// Matches `projection.pca_2d` from Python bit-for-bit on IEEE 754 platforms.
/// Uses `cpyrand` for both power-iteration init and PCA sample selection.
///
/// Returns `(xs, ys)`.
pub fn pca_2d(vectors: &[Vec<f64>]) -> (Vec<f64>, Vec<f64>) {
    let n = vectors.len();
    if n == 0 {
        return (vec![], vec![]);
    }
    let n_dims = vectors[0].len();
    if n_dims < 2 {
        let xs: Vec<f64> = vectors
            .iter()
            .map(|v| if v.is_empty() { 0.0 } else { v[0] })
            .collect();
        let ys = vec![0.0_f64; n];
        return (xs, ys);
    }

    // Centre the data.
    let mut mean = vec![0.0_f64; n_dims];
    for v in vectors {
        for (j, &x) in v.iter().enumerate() {
            mean[j] += x;
        }
    }
    for m in &mut mean {
        *m /= n as f64;
    }
    let centered: Vec<Vec<f64>> = vectors
        .iter()
        .map(|v| v.iter().zip(&mean).map(|(x, m)| x - m).collect())
        .collect();

    // Sample for eigenvector computation.
    // Matches Python: `random.Random(99).sample(range(n), PCA_SAMPLE)`.
    let sample_buf: Vec<Vec<f64>>;
    let sample: &[Vec<f64>] = if n > PCA_SAMPLE {
        let idxs = super::cpyrand::Random::new(99).sample_indices(n, PCA_SAMPLE);
        sample_buf = idxs.into_iter().map(|i| centered[i].clone()).collect();
        &sample_buf
    } else {
        &centered
    };

    // First eigenvector init: matches Python `random.Random(1).gauss(0, 1)` × n_dims.
    let init_e1: Vec<f64> = {
        let mut rng = super::cpyrand::Random::new(1);
        (0..n_dims).map(|_| rng.gauss(0.0, 1.0)).collect()
    };
    let e1 = power_iter(sample, POWER_ITERS, &init_e1);
    if norm(&e1) < 1e-12 {
        return (vec![0.0_f64; n], vec![0.0_f64; n]);
    }

    // Second eigenvector init on the deflated sample: seed 2.
    let sample_d = deflate(sample, &e1);
    let init_e2: Vec<f64> = {
        let mut rng = super::cpyrand::Random::new(2);
        (0..n_dims).map(|_| rng.gauss(0.0, 1.0)).collect()
    };
    let e2 = power_iter(&sample_d, POWER_ITERS, &init_e2);

    let xs: Vec<f64> = centered.iter().map(|row| dot(row, &e1)).collect();
    let ys: Vec<f64> = if norm(&e2) < 1e-12 {
        vec![0.0_f64; n]
    } else {
        centered.iter().map(|row| dot(row, &e2)).collect()
    };

    (xs, ys)
}

/// Return rendering-cap indices for *n* points capped at *max_render*.
///
/// When `n > max_render`, selects `max_render` indices via
/// `cpyrand::Random::new(42).sample_indices(n, max_render)`, matching
/// Python's `random.Random(42).sample(raw, MAX_RENDER)`.
/// When `n <= max_render`, returns the identity range `0..n`.
///
/// The endpoint may call this to determine which subset to render without
/// changing projection order or any cache/HTTP behaviour.
pub fn sample_render_indices(n: usize, max_render: usize) -> Vec<usize> {
    if n > max_render {
        super::cpyrand::Random::new(42).sample_indices(n, max_render)
    } else {
        (0..n).collect()
    }
}

// ── tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // Helper: assert two f64 values are within `tol` of each other.
    fn approx(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() <= tol
    }

    #[test]
    fn decode_le_f32_roundtrip() {
        // 1.0f32 LE = 0x3F800000, 2.0f32 LE = 0x40000000
        let blob: Vec<u8> = vec![0x00, 0x00, 0x80, 0x3f, 0x00, 0x00, 0x00, 0x40];
        let v = decode_vector_le_f32(&blob, 2).expect("should decode");
        assert_eq!(v.len(), 2);
        assert!((v[0] - 1.0_f32).abs() < 1e-7, "v[0]={}", v[0]);
        assert!((v[1] - 2.0_f32).abs() < 1e-7, "v[1]={}", v[1]);
    }

    #[test]
    fn decode_le_f32_too_short_returns_none() {
        let blob: Vec<u8> = vec![0x00, 0x00, 0x80]; // only 3 bytes, need 4 for 1 f32
        assert!(decode_vector_le_f32(&blob, 1).is_none());
    }

    #[test]
    fn dot_basic() {
        assert!(approx(dot(&[1.0, 2.0, 3.0], &[4.0, 5.0, 6.0]), 32.0, 1e-12));
    }

    #[test]
    fn norm_basic() {
        assert!(approx(norm(&[3.0, 4.0]), 5.0, 1e-12));
    }

    #[test]
    fn normalize_unit_vector() {
        let v = normalize(&[3.0, 4.0]);
        assert!(approx(v[0], 0.6, 1e-12));
        assert!(approx(v[1], 0.8, 1e-12));
        assert!(approx(norm(&v), 1.0, 1e-12));
    }

    #[test]
    fn normalize_near_zero_unchanged() {
        let v = normalize(&[0.0, 1e-15]);
        // norm < 1e-12, so original returned unchanged
        assert_eq!(v, vec![0.0, 1e-15]);
    }

    #[test]
    fn mat_vec_basic() {
        let x = vec![vec![1.0, 2.0], vec![3.0, 4.0]];
        let v = vec![1.0, 1.0];
        let r = mat_vec(&x, &v);
        assert_eq!(r, vec![3.0, 7.0]);
    }

    #[test]
    fn mat_t_vec_basic() {
        // X = [[1,2],[3,4]], w = [1,1]  →  X^T w = [1+3, 2+4] = [4, 6]
        let x = vec![vec![1.0, 2.0], vec![3.0, 4.0]];
        let w = vec![1.0, 1.0];
        let r = mat_t_vec(&x, &w);
        assert_eq!(r, vec![4.0, 6.0]);
    }

    #[test]
    fn mat_t_vec_empty_x() {
        let x: Vec<Vec<f64>> = vec![];
        let r = mat_t_vec(&x, &[]);
        assert!(r.is_empty());
    }

    #[test]
    fn deflate_removes_component() {
        // e = [1, 0]; removing x-component leaves only y
        let x = vec![vec![3.0, 4.0], vec![5.0, 6.0]];
        let e = vec![1.0, 0.0];
        let d = deflate(&x, &e);
        assert_eq!(d[0], vec![0.0, 4.0]);
        assert_eq!(d[1], vec![0.0, 6.0]);
    }

    #[test]
    fn power_iter_converges_to_dominant_axis() {
        // X has all variance in the first dimension.
        // X = [[2,0],[-2,0],[1,0],[-1,0]]  →  dominant eigenvector of X^T X is [1,0] or [-1,0].
        let x = vec![
            vec![2.0, 0.0],
            vec![-2.0, 0.0],
            vec![1.0, 0.0],
            vec![-1.0, 0.0],
        ];
        let init = vec![1.0, 1.0];
        let e = power_iter(&x, POWER_ITERS, &init);
        assert_eq!(e.len(), 2);
        // |e[0]| ≈ 1, |e[1]| ≈ 0 (sign flexible)
        assert!(approx(e[0].abs(), 1.0, 1e-10), "e[0]={}", e[0]);
        assert!(approx(e[1].abs(), 0.0, 1e-10), "e[1]={}", e[1]);
    }

    #[test]
    fn power_iter_empty_x_returns_empty() {
        let x: Vec<Vec<f64>> = vec![];
        assert!(power_iter(&x, 10, &[1.0]).is_empty());
    }

    #[test]
    fn pca_2d_empty() {
        let (xs, ys) = pca_2d(&[]);
        assert!(xs.is_empty() && ys.is_empty());
    }

    #[test]
    fn pca_2d_n_dims_lt_2() {
        let vectors = vec![vec![1.0], vec![2.0], vec![3.0]];
        let (xs, ys) = pca_2d(&vectors);
        assert_eq!(xs.len(), 3);
        assert_eq!(ys.len(), 3);
        // xs should equal the raw values, ys should be 0
        assert!(approx(xs[0], 1.0, 1e-12));
        assert!(approx(xs[1], 2.0, 1e-12));
        assert_eq!(ys, vec![0.0, 0.0, 0.0]);
    }

    #[test]
    fn pca_2d_axis_aligned_variance() {
        // All variance in x-axis; mean is 0.
        // Vectors: (2,0),(-2,0),(0,1),(0,-1)
        // X^T X = [[8,0],[0,2]]  →  e1 = [1,0] or [-1,0], e2 = [0,1] or [0,-1]
        let vectors = vec![
            vec![2.0_f64, 0.0],
            vec![-2.0, 0.0],
            vec![0.0, 1.0],
            vec![0.0, -1.0],
        ];
        let (xs, ys) = pca_2d(&vectors);
        assert_eq!(xs.len(), 4);
        assert_eq!(ys.len(), 4);

        // xs projections: |dots with e1| should be [2, 2, 0, 0] in some sign/order
        let s = xs[0].signum(); // consistent sign
        assert!(approx(xs[0], s * 2.0, 1e-6), "xs[0]={}", xs[0]);
        assert!(approx(xs[1], s * -2.0, 1e-6), "xs[1]={}", xs[1]);
        assert!(approx(xs[2].abs(), 0.0, 1e-6), "xs[2]={}", xs[2]);
        assert!(approx(xs[3].abs(), 0.0, 1e-6), "xs[3]={}", xs[3]);

        // ys projections: |dots with e2| should be [0, 0, 1, -1] in some sign
        let s2 = if ys[2].abs() > 1e-9 {
            ys[2].signum()
        } else {
            1.0
        };
        assert!(approx(ys[0].abs(), 0.0, 1e-6), "ys[0]={}", ys[0]);
        assert!(approx(ys[1].abs(), 0.0, 1e-6), "ys[1]={}", ys[1]);
        assert!(approx(ys[2], s2 * 1.0, 1e-6), "ys[2]={}", ys[2]);
        assert!(approx(ys[3], -s2, 1e-6), "ys[3]={}", ys[3]);
    }
}
