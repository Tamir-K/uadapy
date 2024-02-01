import numba
import numpy as np
from scipy.spatial import distance_matrix
from scipy.optimize import minimize
import imuncertain as ua
from scipy.stats import multivariate_normal


def precalculate_constants(normal_distr_spec: np.ndarray) -> tuple:
    d_hi = normal_distr_spec.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi+1)  # array of (d_hi x d_hi) cov matrices and (1 x d_hi) means

    # extract means and covs
    mu = [normal_distr_spec[i, :] for i in range(n)]
    cov = [normal_distr_spec[n+d_hi*i:n+d_hi*(i+1), :] for i in range(n)]

    # compute singular value decomps of covs
    svds = [np.linalg.svd(cov[i], full_matrices=True) for i in range(n)]
    U = [svds[i].U for i in range(n)]
    S = [np.diag(svds[i].S) for i in range(n)]
    Ssqrt = [np.diag(np.sqrt(svds[i].S)) for i in range(n)]

    # combinations used in stress terms
    norm2_mui_sub_muj = [[np.dot(mu[i]-mu[j], mu[i]-mu[j]) for j in range(n)] for i in range(n)]
    Ssqrti_UiTUj_Ssqrtj = [[Ssqrt[i] @ U[i].T @ U[j] @ Ssqrt[j] for j in range(n)] for i in range(n)]
    mui_sub_muj_TUi = [[(mu[i]-mu[j]) @ U[i] for j in range(n)] for i in range(n)]
    mui_sub_muj_TUj = [[(mu[i]-mu[j]) @ U[j] for j in range(n)] for i in range(n)]
    Zij = [[U[i].T @ U[j] for j in range(n)] for i in range(n)]

    # constants = {
    #     'mu': mu,
    #     'cov': cov,
    #     'U': U,
    #     'S': S,
    #     'Ssqrt': Ssqrt,
    #     'norm2_mui_sub_muj': norm2_mui_sub_muj,
    #     'Ssqrti_UiTUj_Ssqrtj': Ssqrti_UiTUj_Ssqrtj,
    #     'mui_sub_muj_TUi': mui_sub_muj_TUi,
    #     'mui_sub_muj_TUj': mui_sub_muj_TUj,
    #     'Zij': Zij
    # }
    constants = (
        np.stack(mu),
        np.stack(cov),
        np.stack(U),
        np.stack(S),
        np.stack(Ssqrt),
        np.stack(norm2_mui_sub_muj),
        np.stack(Ssqrti_UiTUj_Ssqrtj),
        np.stack(mui_sub_muj_TUi),
        np.stack(mui_sub_muj_TUj),
        np.stack(Zij)
    )
    return constants


@numba.njit()
def stress_ij(i: int, j: int, normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray, 
        #pre,
        mu,
        cov,
        U,
        S,
        Ssqrt,
        norm2_mui_sub_muj,
        Ssqrti_UiTUj_Ssqrtj,
        mui_sub_muj_TUi,
        mui_sub_muj_TUj,
        Zij
) -> float:
    d_hi = normal_distr_spec.shape[1]
    d_lo = uamds_transforms.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)
    # get constants
    # (
    #     mu,
    #     cov,
    #     U,
    #     S,
    #     Ssqrt,
    #     norm2_mui_sub_muj,
    #     Ssqrti_UiTUj_Ssqrtj,
    #     mui_sub_muj_TUi,
    #     mui_sub_muj_TUj,
    #     Zij
    # ) = pre

    # get some objects for i
    Si = S[i]
    Ssqrti = Ssqrt[i]
    ci = uamds_transforms[i, :]
    Bi = uamds_transforms[n+i*d_hi : n+(i+1)*d_hi, :]

    # get some objects for j
    Sj = S[j]
    Ssqrtj = Ssqrt[j]
    cj = uamds_transforms[j, :]
    Bj = uamds_transforms[n+j*d_hi : n+(j+1)*d_hi, :]

    ci_sub_cj = ci-cj

    # compute term 1 : part 1 : ||Si - Si^(1/2)Bi^T BiSi^(1/2)||_F^2
    temp = Ssqrti @ Bi
    temp = Si - (temp @ temp.T)
    part1 = (temp*temp).sum()  # sum of squared elements = squared frobenius norm
    # compute term 1 : part 2 : same as part 1 but with j
    temp = Ssqrtj @ Bj
    temp = Sj - (temp @ temp.T)
    part2 = (temp*temp).sum()  # sum of squared elements = squared frobenius norm
    # compute term 1 : part 3
    temp = (Ssqrti @ Bi) @ (Bj.T @ Ssqrtj)  # outer product of transformed Bs
    temp = Ssqrti_UiTUj_Ssqrtj[i][j] - temp
    part3 = (temp*temp).sum()  # sum of squared elements = squared frobenius norm
    term1 = 2*(part1+part2)+4*part3

    # compute term 2 : part 1 : sum_k^n [ Si_k * ( <Ui_k, mui-muj> - <Bi_k, ci-cj> )^2 ]
    temp = ci_sub_cj @ Bi.T
    temp = mui_sub_muj_TUi[i][j] - temp
    temp = temp*temp  # squared
    part1 = (temp @ Si).sum()
    # compute term 2 : part 2 : same as part 1 but with j
    temp = ci_sub_cj @ Bj.T
    temp = mui_sub_muj_TUj[i][j] - temp
    temp = temp*temp  # squared
    part2 = (temp @ Sj).sum()
    term2 = part1+part2

    # compute term 3 : part 1
    norm1 = norm2_mui_sub_muj[i][j]
    norm2 = np.dot(ci_sub_cj,ci_sub_cj)  # squared norm
    part1 = norm1-norm2
    # compute term 3 : part 2
    part2 = 0
    part3 = 0
    for k in range(d_hi):
        sigma_i = Si[k, k]
        sigma_j = Sj[k, k]
        bik = Bi[k, :]
        bjk = Bj[k, :]
        part2 += (1 - np.dot(bik,bik))*sigma_i
        part3 += (1 - np.dot(bjk,bjk))*sigma_j
    term3 = (part1 + part2 + part3)**2

    return term1+term2+term3


# @numba.njit()
# def gradient_ij_nocopy(i: int, j: int, normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray,
#                 S, norm2_mui_sub_muj, mui_sub_muj_TUi, mui_sub_muj_TUj, Z) -> tuple:
#     d_hi = normal_distr_spec.shape[1]
#     # d_lo = uamds_transforms.shape[1]
#     n = normal_distr_spec.shape[0] // (d_hi + 1)
#     # get some objects for i
#     Si = S[i]
#     # mui = mu[i]
#     ci = uamds_transforms[i, :]
#     Bi = uamds_transforms[n+i*d_hi : n+(i+1)*d_hi, :].T
#     BiSi = Bi @ Si

#     # get some objects for j
#     Sj = S[j]
#     # muj = mu[j]
#     cj = uamds_transforms[j, :]
#     Bj = uamds_transforms[n+j*d_hi:n+(j+1)*d_hi, :].T
#     BjSj = Bj @ Sj

#     # mui_sub_muj = mui - muj
#     ci_sub_cj = ci - cj

#     # compute term 1 :
#     Zij = Z[i][j]
#     part1i = (BiSi @ Bi.T @ BiSi) - (BiSi @ Si)
#     part1j = (BjSj @ Bj.T @ BjSj) - (BjSj @ Sj)
#     part2i = (BjSj @ Bj.T @ BiSi) - (BjSj @ Zij.T @ Si)
#     part2j = (BiSi @ Bi.T @ BjSj) - (BiSi @ Zij   @ Sj)
#     dBi = (part1i + part2i) * 8
#     dBj = (part1j + part2j) * 8

#     # compute term 2 :
#     dci = np.zeros(ci.shape)
#     dcj = np.zeros(cj.shape)
#     if i != j:
#         # gradient part for B matrices
#         part3i = (np.outer(ci_sub_cj, (ci_sub_cj @ Bi)) - np.outer(ci_sub_cj, mui_sub_muj_TUi[i][j])) @ Si
#         part3j = (np.outer(ci_sub_cj, (ci_sub_cj @ Bj)) - np.outer(ci_sub_cj, mui_sub_muj_TUj[i][j])) @ Sj
#         dBi += 2*part3i
#         dBj += 2*part3j
#         # gradient part for c vectors
#         part4i = (mui_sub_muj_TUi[i][j] - (ci_sub_cj @ Bi)) @ BiSi.T
#         part4j = (mui_sub_muj_TUj[i][j] - (ci_sub_cj @ Bj)) @ BjSj.T
#         part4 = -2*(part4i+part4j)
#         dci += part4
#         dcj -= part4

#     # compute term 3 :
#     norm1 = norm2_mui_sub_muj[i][j]
#     norm2 = np.dot(ci_sub_cj, ci_sub_cj)
#     part1 = norm1-norm2
#     part2 = part3 = 0.0
#     for k in range(d_hi):
#         sigma_i = Si[k, k]
#         sigma_j = Sj[k, k]
#         bik = Bi[:, k]
#         bjk = Bj[:, k]
#         part2 += (1 - np.dot(bik, bik)) * sigma_i
#         part3 += (1 - np.dot(bjk, bjk)) * sigma_j
#     term3 = -4 * (part1 + part2 + part3)
#     dBi += BiSi * term3
#     dBj += BjSj * term3

#     if i != j:
#         dci += ci_sub_cj * term3
#         dcj -= ci_sub_cj * term3

#     return dBi.T, dBj.T, dci, dcj


# with copy
@numba.njit()
def gradient_ij(i: int, j: int, normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray,
                S, norm2_mui_sub_muj, mui_sub_muj_TUi, mui_sub_muj_TUj, Z) -> tuple:
    d_hi = normal_distr_spec.shape[1]
    # d_lo = uamds_transforms.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)
    # get some objects for i
    Si = S[i].copy()
    # mui = mu[i]
    ci = uamds_transforms[i, :]
    Bi = uamds_transforms[n+i*d_hi : n+(i+1)*d_hi, :].T.copy()
    BiSi = Bi @ Si

    # get some objects for j
    Sj = S[j].copy()
    # muj = mu[j]
    cj = uamds_transforms[j, :]
    Bj = uamds_transforms[n+j*d_hi:n+(j+1)*d_hi, :].T.copy()
    BjSj = Bj @ Sj

    # mui_sub_muj = mui - muj
    ci_sub_cj = ci - cj

    # compute term 1 :
    Zij = Z[i][j].copy()
    BiT = Bi.T.copy()
    BjT = Bj.T.copy()
    part1i = (BiSi @ BiT @ BiSi) - (BiSi @ Si)
    part1j = (BjSj @ BjT @ BjSj) - (BjSj @ Sj)
    part2i = (BjSj @ BjT @ BiSi) - (BjSj @ Zij.T @ Si)
    part2j = (BiSi @ BiT @ BjSj) - (BiSi @ Zij   @ Sj)
    dBi = (part1i + part2i) * 8
    dBj = (part1j + part2j) * 8

    # compute term 2 :
    dci = np.zeros(ci.shape)
    dcj = np.zeros(cj.shape)
    if i != j:
        # gradient part for B matrices
        part3i = (np.outer(ci_sub_cj, (ci_sub_cj @ Bi)) - np.outer(ci_sub_cj, mui_sub_muj_TUi[i][j])) @ Si
        part3j = (np.outer(ci_sub_cj, (ci_sub_cj @ Bj)) - np.outer(ci_sub_cj, mui_sub_muj_TUj[i][j])) @ Sj
        dBi += 2*part3i
        dBj += 2*part3j
        # gradient part for c vectors
        part4i = (mui_sub_muj_TUi[i][j] - (ci_sub_cj @ Bi)) @ BiSi.T
        part4j = (mui_sub_muj_TUj[i][j] - (ci_sub_cj @ Bj)) @ BjSj.T
        part4 = -2*(part4i+part4j)
        dci += part4
        dcj -= part4

    # compute term 3 :
    norm1 = norm2_mui_sub_muj[i][j]
    norm2 = np.dot(ci_sub_cj, ci_sub_cj)
    part1 = norm1-norm2
    part2 = part3 = 0.0
    for k in range(d_hi):
        sigma_i = Si[k, k]
        sigma_j = Sj[k, k]
        bik = Bi[:, k].copy()
        bjk = Bj[:, k].copy()
        part2 += (1 - np.dot(bik, bik)) * sigma_i
        part3 += (1 - np.dot(bjk, bjk)) * sigma_j
    term3 = -4 * (part1 + part2 + part3)
    dBi += BiSi * term3
    dBj += BjSj * term3

    if i != j:
        dci += ci_sub_cj * term3
        dcj -= ci_sub_cj * term3

    return dBi.T, dBj.T, dci, dcj



def stress(normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray, precalc_constants: tuple=None) -> float:
    d_hi = normal_distr_spec.shape[1]
    d_lo = uamds_transforms.shape[1]
    n = normal_distr_spec.shape[0]//(d_hi+1)  # array of (d_hi x d_hi) cov matrices and (1 x d_hi) means

    if precalc_constants is None:
        precalc_constants = precalculate_constants(normal_distr_spec)

    sum = 0
    for i in range(n):
        for j in range(i, n):
            sum += stress_ij(i, j, normal_distr_spec, uamds_transforms, *precalc_constants)
    return sum


@numba.njit(parallel=True)
def gradient_numba(normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray, S, norm2_mui_sub_muj,
                   mui_sub_muj_TUi, mui_sub_muj_TUj, Z, n, d_hi):
    # compute the gradients of all affine transforms
    grad = np.zeros(uamds_transforms.shape)
    for i in numba.prange(n):
        for j in numba.prange(i, n):
            dBi, dBj, dci, dcj = gradient_ij(i, j, normal_distr_spec, uamds_transforms, S, norm2_mui_sub_muj,
                                             mui_sub_muj_TUi, mui_sub_muj_TUj, Z)
            # c gradients on top part of matrix
            grad[i, :] += dci
            grad[j, :] += dcj
            # B gradients below c part of matrix
            grad[n + i * d_hi:n + (i + 1) * d_hi, :] += dBi
            grad[n + j * d_hi:n + (j + 1) * d_hi, :] += dBj
    return grad


def gradient(normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray, precalc_constants: tuple) -> np.ndarray:
    # print("grad")
    d_hi = normal_distr_spec.shape[1]
    # d_lo = uamds_transforms.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)

    S = precalc_constants[3]
    norm2_mui_sub_muj = precalc_constants[5]
    mui_sub_muj_TUi = precalc_constants[7]
    mui_sub_muj_TUj = precalc_constants[8]
    Z = precalc_constants[9]

    # print(S.shape, norm2_mui_sub_muj.shape, mui_sub_muj_TUi.shape, mui_sub_muj_TUj.shape, Z.shape)

    return gradient_numba(normal_distr_spec, uamds_transforms, S, norm2_mui_sub_muj,
                   mui_sub_muj_TUi, mui_sub_muj_TUj, Z, n, d_hi)


def iterate_simple_gradient_descent(
        normal_distr_spec: np.ndarray,
        uamds_transforms_init: np.ndarray,
        precalc_constants: tuple = None,
        num_iter: int = 10,
        a: float = 0.0001
) -> np.ndarray:

    if precalc_constants is None:
        precalc_constants = precalculate_constants(normal_distr_spec)

    # gradient descent
    uamds_transforms = uamds_transforms_init
    for i in range(num_iter):
        grad = gradient(normal_distr_spec, uamds_transforms, precalc_constants)
        uamds_transforms -= grad * a
    return uamds_transforms


def iterate_scipy(
        normal_distr_spec: np.ndarray,
        uamds_transforms_init: np.ndarray,
        precalc_constants: tuple = None
) -> np.ndarray:
    if precalc_constants is None:
        precalc_constants = precalculate_constants(normal_distr_spec)
    pre = precalc_constants

    # gradient descent
    x_shape = uamds_transforms_init.shape
    n_elems = uamds_transforms_init.size

    def fx(x: np.ndarray):
        return stress(normal_distr_spec, x.reshape(x_shape), pre)

    def dfx(x: np.ndarray):
        grad = gradient(normal_distr_spec, x.reshape(x_shape), pre)
        return grad.flatten()

    #err = scipy.optimize.check_grad(fx, dfx, uamds_transforms.reshape(uamds_transforms.size))
    solution = minimize(fx, uamds_transforms_init.flatten(), method='BFGS', jac=dfx)
    return solution.x.reshape(x_shape)




def perform_projection(normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray) -> np.ndarray:
    d_hi = normal_distr_spec.shape[1]
    # d_lo = uamds_transforms.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)

    mus = []
    covs = []
    for i in range(n):
        mu_lo = uamds_transforms[i, :]
        cov_hi = normal_distr_spec[n+i*d_hi : n+(i+1)*d_hi, :]
        B = uamds_transforms[n+i*d_hi : n+(i+1)*d_hi, :]
        S = np.diag(np.linalg.svd(cov_hi, full_matrices=True).S)
        cov_lo = B.T @ S @ B
        mus.append(mu_lo)
        covs.append(cov_lo)
    return mk_normal_distr_spec(mus, covs)



def apply_uamds(means: list[np.ndarray], covs: list[np.ndarray], target_dim=2) -> dict[str, list[np.ndarray] | float]:
    normal_distr_spec = mk_normal_distr_spec(means, covs)
    d_hi = normal_distr_spec.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)
    # initialization
    uamds_transforms = np.random.rand(normal_distr_spec.shape[0], target_dim)
    avg_dist_hi = distance_matrix(normal_distr_spec[:n,:], normal_distr_spec[:n,:]).mean()
    avg_dist_lo = distance_matrix(uamds_transforms[:n,:], uamds_transforms[:n,:]).mean()
    uamds_transforms[:n,:] *= (avg_dist_hi/avg_dist_lo)
    # compute UAMDS
    pre = precalculate_constants(normal_distr_spec)
    uamds_transforms = iterate_scipy(normal_distr_spec, uamds_transforms, pre)
    s = stress(normal_distr_spec, uamds_transforms, pre)
    # perform projection
    normal_distribs_lo = perform_projection(normal_distr_spec, uamds_transforms)
    means_lo, covs_lo = get_means_covs(normal_distribs_lo)
    affine_transforms = convert_xform_uamds_to_affine(normal_distr_spec, uamds_transforms)
    translations = affine_transforms[:n,:]
    translations = [translations[i, :] for i in range(n)]
    projection_matrices = affine_transforms[n:,:]
    projection_matrices = [projection_matrices[i*d_hi:(i+1)*d_hi, :] for i in range(n)]
    return {'means': means_lo, 'covs':covs_lo, 'translations': translations, 'projections': projection_matrices, 'stress': s}



def uamds(distributions, dims: int):
    """
    Applies UAMDS algorithm to the distribution and returns the distribution
    in lower-dimensional space. It assumes a normal distributions. If you apply
    other distributions that provide mean and covariance, these values would be used
    to approximate a normal distribution
    :param distributions: List of input distributions
    :param dims: Target dimension
    :return: List of distributions in low-dimensional space
    """
    try:
        means = np.array([d.mean() for d in distributions])
        covs = np.array([d.cov() for d in distributions])
        result = apply_uamds(means, covs, dims)
        distribs_lo = []
        for (m, c) in zip(result['means'], result['covs']):
            distribs_lo.append(ua.distribution.distribution(multivariate_normal(m, c)))
        return distribs_lo
    except Exception as e:
        raise Exception(f'Something went wrong. Did you input normal distributions? Exception:{e}')



####################################
# utility methods ##################
####################################

def get_means_covs(normal_distr_spec: np.ndarray) -> tuple[list[np.ndarray], list[np.ndarray]]:
    d_hi = normal_distr_spec.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)
    means = []
    covs = []
    for i in range(n):
        means.append(normal_distr_spec[i, :])
        covs.append(normal_distr_spec[n+i*d_hi : n+(i+1)*d_hi, :])
    return means, covs


def mk_normal_distr_spec(means: list[np.ndarray], covs: list[np.ndarray]) -> np.ndarray:
    mean_block = np.vstack(means)
    cov_block = np.vstack(covs)
    return np.vstack([mean_block, cov_block])


def convert_xform_uamds_to_affine(normal_distr_spec: np.ndarray, uamds_transforms: np.ndarray) -> np.ndarray:
    d_hi = normal_distr_spec.shape[1]
    # d_lo = uamds_transforms.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)

    translations = []
    projections = []
    for i in range(n):
        mu_lo = uamds_transforms[i, :]
        mu_hi = normal_distr_spec[i, :]
        B = uamds_transforms[n+i*d_hi : n+(i+1)*d_hi, :]
        cov_hi = normal_distr_spec[n+i*d_hi : n+(i+1)*d_hi, :]
        U = np.linalg.svd(cov_hi, full_matrices=True).U
        P = U @ B
        t = mu_lo - (mu_hi @ P)
        translations.append(t)
        projections.append(P)
    return np.vstack([np.vstack(translations), np.vstack(projections)])
        

def convert_xform_affine_to_uamds(normal_distr_spec: np.ndarray, affine_transforms: np.ndarray) -> np.ndarray:
    d_hi = normal_distr_spec.shape[1]
    # d_lo = affine_transforms.shape[1]
    n = normal_distr_spec.shape[0] // (d_hi + 1)

    mus_lo = []
    Bs = []
    for i in range(n):
        t = affine_transforms[i, :]
        mu_hi = normal_distr_spec[i, :]
        P = affine_transforms[n+i*d_hi : n+(i+1)*d_hi, :]
        cov_hi = normal_distr_spec[n+i*d_hi : n+(i+1)*d_hi, :]
        U = np.linalg.svd(cov_hi, full_matrices=True).U
        B = U.T @ P
        mu_lo = (mu_hi @ P) + t
        mus_lo.append(mu_lo)
        Bs.append(B)
    return np.vstack([np.vstack(mus_lo), np.vstack(Bs)])

