#!/usr/bin/env python

# wujian@2018
"""
Compute some typical spatial features(SRP/IPD/MSC)
"""

import argparse

import numpy as np
from distutils.util import strtobool

from libs.utils import get_logger, nextpow2
from libs.opts import StftParser, str2tuple
from libs.data_handler import SpectrogramReader, ArchiveWriter
from libs.spatial import srp_phat_linear, ipd, msc

logger = get_logger(__name__)


def compute_spatial_feats(args, S):
    if args.type == "srp":
        num_ffts = nextpow2(
            args.frame_len) if args.round_power_of_two else args.frame_len
        srp_kwargs = {
            "sample_frequency": args.samp_frequency,
            "num_doa": args.num_doa,
            "num_bins": num_ffts // 2 + 1,
            "samp_doa": not args.samp_tdoa
        }
        return srp_phat_linear(S, args.linear_topo, **srp_kwargs)
    elif args.type == "ipd":
        if S.ndim < 3:
            raise ValueError("Only one-channel STFT available")
        ipd_list = []
        for p in args.ipd_pair.split(";"):
            indexes = list(map(int, p.split(",")))
            if len(indexes) != 2:
                raise ValueError("Invalid --ipd.pair configuration " +
                                 f"detected: {args.ipd_pair}")
            L, R = indexes
            if R > S.shape[0]:
                raise RuntimeError(f"Could not access channel {R}")
            ipd_mat = ipd(S[L], S[R], cos=args.ipd_cos, sin=args.ipd_sin)
            ipd_list.append(ipd_mat)
        # concat along frequency axis
        return np.hstack(ipd_list)
    else:
        return msc(S, context=args.msc_ctx)


def run(args):
    stft_kwargs = {
        "frame_len": args.frame_len,
        "frame_hop": args.frame_hop,
        "round_power_of_two": args.round_power_of_two,
        "window": args.window,
        "center": args.center,  # false to comparable with kaldi
        "transpose": True  # T x F
    }
    spectrogram_reader = SpectrogramReader(args.wav_scp, **stft_kwargs)

    num_utts = 0
    with ArchiveWriter(args.dup_ark, args.scp) as writer:
        for key, spectrogram in spectrogram_reader:
            # spectrogram: shape NxTxF
            feats = compute_spatial_feats(args, spectrogram)
            # feats: T x F
            writer.write(key, feats)
            num_utts += 1
            if not num_utts % 1000:
                logger.info(f"Processed {num_utts} utterance...")
    logger.info(f"Processed {args.type.upper()} for {num_utts} utterances")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=
        "Command to compute some typical spatial features, egs: SRP/MSC/IPD. ("
        "SRP: SRP-PHAT Anguler Spectrum, MSC: Magnitude Squared Coherence, "
        "IPD: Interchannel Phase Difference)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[StftParser.parser])
    parser.add_argument("wav_scp",
                        type=str,
                        help="Multi-Channel wave scripts in kaldi format")
    parser.add_argument("dup_ark",
                        type=str,
                        help="Location to dump features in kaldi's archives")
    parser.add_argument("--scp",
                        type=str,
                        default="",
                        help="If assigned, generate corresponding "
                        "feature scripts")
    parser.add_argument("--type",
                        type=str,
                        default="srp",
                        choices=["srp", "msc", "ipd"],
                        help="Type of spatial features to compute")
    parser.add_argument("--srp.sample-rate",
                        type=int,
                        dest="samp_frequency",
                        default=16000,
                        help="Sample frequency of input wave")
    parser.add_argument("--srp.sample-tdoa",
                        type=strtobool,
                        default=False,
                        dest="samp_tdoa",
                        help="Sample TDoA instead of DoA "
                        "when computing spectrum")
    parser.add_argument("--srp.num_doa",
                        type=int,
                        dest="num_doa",
                        default=181,
                        help="Number of DoA to sampled from 0 to 180 degress")
    parser.add_argument("--srp.topo",
                        type=str2tuple,
                        dest="linear_topo",
                        default="0,0.2,0.4,0.8",
                        help="Topology description of microphone arrays")
    parser.add_argument("--ipd.cos",
                        dest="ipd_cos",
                        type=strtobool,
                        default=False,
                        help="Compute cosIPD instead of IPD")
    parser.add_argument("--ipd.sin",
                        dest="ipd_sin",
                        type=strtobool,
                        default=False,
                        help="Append sinIPD to cosIPD spatial features")
    parser.add_argument("--ipd.pair",
                        type=str,
                        dest="ipd_pair",
                        default="0,1",
                        help="Given several channel index "
                        "pairs to compute IPD spatial features, "
                        "separated by semicolon, egs: 0,3;1,4")
    parser.add_argument("--msc.ctx",
                        type=int,
                        dest="msc_ctx",
                        default=1,
                        help="Value of context in MSC computation")
    args = parser.parse_args()
    run(args)
