#!/usr/bin/env python

# wujian@2018
"""
AuxIVA: 
    Ono N. Stable and fast update rules for independent vector analysis 
    based on auxiliary function technique[C]//Applications of Signal 
    Processing to Audio and Acoustics (WASPAA), 2011 IEEE Workshop on. IEEE, 2011: 189-192.
Reference: https://github.com/LCAV/pyroomacoustics/blob/master/pyroomacoustics/bss/auxiva.py
"""

import argparse
from pathlib import Path

import numpy as np

from libs.data_handler import SpectrogramReader
from libs.opts import StftParser
from libs.utils import inverse_stft, get_logger, write_wav, EPSILON

logger = get_logger(__name__)


def auxiva(X, epochs=20):
    """
    Arguments:
        X: shape in N x T x F
    Return
        Y: same shape as X
    """
    N, T, F = X.shape
    # X: F x T x N
    X = X.transpose([2, 1, 0])
    # F x N x N
    W = np.array([np.eye(N, dtype=np.complex) for f in range(F)])
    I = np.eye(N)
    # Y: F x T x N
    Y = np.einsum("...tn,...nx->...tx", X, np.conj(W))

    for _ in range(epochs):
        # T x N
        R = np.sqrt(np.sum(np.abs(Y) ** 2, axis=0))
        # N x T
        Gr = 1 / (R.T + EPSILON)
        for f in range(F):
            for n in range(N):
                # compute V
                V = (np.dot(np.expand_dims(Gr[n], 0) * X[f].T, np.conj(
                    X[f]))) / T
                # update W
                w = np.linalg.solve(np.conj(W[f].T) @ V, I[n])
                W[f, :, n] = w / np.inner(np.conj(w), V @ w)

        Y = np.einsum("...tn,...nx->...tx", X, np.conj(W))
    # F x T x N => N x T x F
    Y = np.transpose(Y, [2, 1, 0])
    return Y


def run(args):
    stft_kwargs = {
        "frame_len": args.frame_len,
        "frame_hop": args.frame_hop,
        "window": args.window,
        "center": args.center,
        "transpose": True  # F x T instead of T x F
    }

    spectrogram_reader = SpectrogramReader(
        args.wav_scp,
        round_power_of_two=args.round_power_of_two,
        **stft_kwargs)
    for key, spectrogram in spectrogram_reader:
        logger.info(f"Processing utterance {key}...")
        separated = auxiva(spectrogram, args.epochs)
        norm = spectrogram_reader.maxabs(key)
        for idx in range(separated.shape[0]):
            samps = inverse_stft(separated[idx], **stft_kwargs, norm=norm)
            fname = Path(args.dst_dir) / f"{key}.src{idx + 1}.wav"
            write_wav(fname, samps, sr=args.sr)
    logger.info(f"Processed {len(spectrogram_reader)} utterances")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Command to do AuxIVA bss algorithm",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[StftParser.parser])
    parser.add_argument("wav_scp",
                        type=str,
                        help="Multi-channel wave scripts in kaldi format")
    parser.add_argument("dst_dir",
                        type=str,
                        help="Location to dump separated source files")
    parser.add_argument("--num-epochs",
                        default=20,
                        type=int,
                        dest="epochs",
                        help="Number of epochs to run AuxIVA algorithm")
    parser.add_argument("--sr",
                        type=int,
                        default=16000,
                        help="Waveform data sample rate")
    args = parser.parse_args()
    run(args)
