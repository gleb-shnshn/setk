#!/usr/bin/env python

# wujian@2019
"""
Sound Source Localization (SSL) Module
"""
import numpy as np

from .utils import cmat_abs


def ml_ssl(stft, sv, compression=0, eps=1e-8, norm=False, mask=None):
    """
    Maximum likelihood SSL
    Arguments:
        stft: STFT transform result, M x T x F
        sv: steer vector in each directions, A x M x F
        norm: normalze STFT or not
        mask: TF-mask for source, T x F x (N)
    Return:
        index: DoA index
    """
    _, T, F = stft.shape
    if mask is None:
        mask = np.ones([T, F])
    # make sure sv is normalized
    sv = sv / np.linalg.norm(sv, axis=1, keepdims=True)
    if norm:
        stft = stft / np.maximum(cmat_abs(stft), eps)
    ssh_cor = np.abs(np.einsum("mtf,mtf->tf", stft, stft.conj()))
    ssv_cor = np.abs(np.einsum("amf,mtf->atf", sv, stft.conj())) ** 2
    # A x T x F
    delta = ssh_cor[None, ...] - ssv_cor / (1 + eps)
    if compression <= 0:
        tf_loglike = -np.log(np.maximum(delta, eps))
    else:
        tf_loglike = -np.power(delta, compression)
    # masking
    if mask.ndim == 2:
        loglike = np.sum(mask[None, ...] * tf_loglike, (1, 2))
    else:
        loglike = np.einsum("ntf,atf->na", mask, tf_loglike)
    return np.argmax(loglike, axis=-1)


def srp_ssl(stft, sv, srp_pair=None, mask=None):
    """
    Do SRP-PHAT based SSL
    Arguments:
        stft: STFT transform result, M x T x F
        sv: steer vector in each directions, A x M x F
        srp_pair: index pair to compute srp response
        mask: TF-mask for source, T x F
    Return:
        index: DoA index
    """
    if srp_pair is None:
        raise ValueError("srp_pair cannot be None, (list, list)")
    _, T, F = stft.shape
    if mask is None:
        mask = np.ones([T, F])
    index_l, index_r = srp_pair
    # M x T x F
    obs_pha = np.angle(stft)
    # A x M x F
    ora_pha = np.angle(sv)
    # observed ipd: P x T x F
    obs_ipd = obs_pha[index_l] - obs_pha[index_r]
    # oracle ipd: A x P x F
    ora_ipd = ora_pha[:, index_l] - ora_pha[:, index_r]
    # directional feature: A x P x T x F
    af = np.cos(obs_ipd[None, ...] - ora_ipd[..., None, :])
    # mean: A x T x F
    af = np.mean(af, 1)
    # mask and sum: A
    srp = np.sum(af * mask[None, ...], (1, 2))
    return np.argmax(srp)


def music_ssl(stft, sv, mask=None):
    """
    Do MUSIC based SSL
    Arguments:
        stft: STFT transform result, M x T x F
        sv: steer vector in each directions, A x M x F
        mask: TF-mask for source, T x F
    Return:
        index: DoA index
    """
    _, T, F = stft.shape
    if mask is None:
        mask = np.ones([T, F])
    # F x M x T
    obs = np.transpose(stft * mask, (2, 0, 1))
    # F x M x M
    obs_covar = np.einsum("...at,...bt->...ab", obs, obs.conj()) / T
    # w: ascending order
    _, v = np.linalg.eigh(obs_covar)
    # F x M x M - 1
    noise_sub = v[..., :-1]
    # F x M x M
    noise_covar = np.einsum("...at,...bt->...ab", noise_sub, noise_sub.conj())
    # F x A x M
    sv = np.transpose(sv, (2, 0, 1))
    # F x A
    denorm = np.einsum("...a,...ab,...b->...", sv.conj(), noise_covar[:, None],
                       sv)
    # A
    score = np.sum(np.abs(denorm), axis=0)
    return np.argmin(score)
