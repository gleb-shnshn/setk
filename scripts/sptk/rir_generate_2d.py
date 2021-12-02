#!/usr/bin/env python

# wujian@2019
"""
Generate scripts for RIR simulation which use
    1) rir-simulate (see src/rir-simulate.cc)
    2) pyrirgen (see https://github.com/Marvin182/rir-generator)
    3) gpurir (see https://github.com/DavidDiazGuerra/gpuRIR)
"""
import os
import json
import shutil
import random
import argparse
from distutils.util import strtobool

from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

from libs.data_handler import run_command
from libs.utils import get_logger, write_wav
from libs.opts import str2tuple
from libs.sampler import UniformSampler

try:
    import pyrirgen
    pyrirgen_available = True
except ImportError:
    pyrirgen_available = False

try:
    import gpuRIR as pygpurir
    gpu_rir_available = True
    pygpurir.activateMixedPrecision(False)
    pygpurir.activateLUT(True)
except ImportError:
    gpu_rir_available = False

if shutil.which("rir-simulate"):
    cpp_rir_available = True
else:
    cpp_rir_available = False

plt.switch_backend("agg")

default_dpi = 200
default_fmt = "jpg"

logger = get_logger(__name__)


class Room(object):
    """
    Room instance
    """
    def __init__(self, l, w, h, rt60=None, refl=None):
        self.size = (l, w, h)
        self.beta = rt60 if rt60 is not None else [refl] * 6
        self.memo = "{}={:.2f}".format("RT60" if rt60 is not None else "Refl",
                                       rt60 if rt60 is not None else refl)

    def set_mic(self, topo, center):
        """
        Place microphone array
        topo: tuple like [(x1, y1), ...]
        center: center 3D postion for microphone array
        """
        Mx, My, Mz = center
        self.rpos = [(Mx + x, My + y, Mz) for (x, y) in topo]
        self.topo = topo
        self.rcen = (Mx, My)

    def set_spk(self, pos):
        """
        Place sound source (speaker)
        """
        self.spos = pos

    def conf(self):
        """
        Return configure of room (exclude sound sources)
        """
        Rf = lambda f: round(f, 3)
        return {
            "beta": [Rf(f) for f in self.beta]
            if isinstance(self.beta, list) else Rf(self.beta),
            "receiver_location": [tuple(Rf(n) for n in p) for p in self.rpos],
            "room_size": [Rf(n) for n in self.size],
            "receiver_geometric":
            self.topo
        }

    def plot(self, scfg, dest, room_id):
        """
        Visualize microphone array and speakers in current room
        """
        fig, ax = plt.subplots()
        ax.set_aspect("equal", "box")
        # constraint length and width
        l, w, _ = self.size
        ax.set_xlim((0, l))
        ax.set_ylim((0, w))
        # draw microphone array
        ax.plot([p[0] for p in self.rpos], [p[1] for p in self.rpos], "k.")
        ax.plot([self.rcen[0]], [self.rcen[1]], "r+")
        # draw each speaker
        spkx = [cfg["pos"][0] for cfg in scfg]
        spky = [cfg["pos"][1] for cfg in scfg]
        ax.plot(spkx, spky, "k+")
        ax.set_xlabel(f"Length ({l:.2f}m)")
        ax.set_xticks([round(x, 1) for x in np.linspace(0, l, 5)])
        ax.set_ylabel(f"Width ({w:.2f}m)")
        ax.set_yticks([round(y, 1) for y in np.linspace(0, w, 5)])
        ax.set_title(f"{room_id} ({self.memo})")
        fig.savefig(dest, dpi=default_dpi, format=default_fmt)
        plt.close(fig)

    def rir(self, fname, sr=16000, rir_nsamps=4096, v=340, gpu=False):
        """
        Generate rir for current settings
        """
        if gpu:
            # self.beta: rt60
            # beta: reflection coefficients
            beta = pygpurir.beta_SabineEstimation(self.size, self.beta)
            # NOTE: do not clear here
            # diff = pygpurir.att2t_SabineEstimator(15, self.beta)
            tmax = rir_nsamps / sr
            nb_img = pygpurir.t2n(tmax, self.size)
            # S x R x T
            rirs = pygpurir.simulateRIR(self.size,
                                        beta,
                                        np.array(self.spos),
                                        np.array(self.rpos),
                                        nb_img,
                                        tmax,
                                        sr,
                                        mic_pattern="omni")
            S, _, _ = rirs.shape
            for s in range(S):
                write_wav(f"{fname}-{s + 1}.wav", rirs[s], sr=sr)
        elif cpp_rir_available:
            # format float
            ffloat = lambda f: "{:.3f}".format(f)
            # location for each microphone
            loc_for_each_channel = [
                ",".join(map(ffloat, p)) for p in self.rpos
            ]
            beta = ",".join(map(ffloat, self.beta)) if isinstance(
                self.beta, list) else round(self.beta, 3)
            run_command(
                "rir-simulate --sound-velocity={v} --samp-frequency={sample_rate} "
                "--hp-filter=true --number-samples={rir_samples} --beta={beta} "
                "--room-topo={room_size} --receiver-location=\"{receiver_location}\" "
                "--source-location={source_location} {dump_dest}".format(
                    v=v,
                    sample_rate=sr,
                    rir_samples=rir_nsamps,
                    room_size=",".join(map(ffloat, self.size)),
                    beta=beta,
                    receiver_location=";".join(loc_for_each_channel),
                    source_location=",".join(map(ffloat, self.spos)),
                    dump_dest=fname))
        elif pyrirgen_available:
            rir = pyrirgen.generateRir(self.size,
                                       self.spos,
                                       self.rpos,
                                       soundVelocity=v,
                                       fs=sr,
                                       nDim=3,
                                       nSamples=rir_nsamps,
                                       nOrder=-1,
                                       reverbTime=self.beta,
                                       micType="omnidirectional",
                                       isHighPassFilter=True)
            if isinstance(rir, list):
                rir = np.stack(rir)
            write_wav(fname, rir, sr=sr)
        else:
            raise RuntimeError("Both rir-simulate and pyrirgen unavailable")


class RoomGenerator(object):
    """
    Room generator
    """
    def __init__(self, rt60_opt, absc_opt, room_dim):
        """
        rt60_opt: "" or "a,b", higher priority than absc_opt
        absc_opt: tuple like (a,b)
        room_dim: str like "a,b;c,d;e,d"
        """
        self.rt60_opt = rt60_opt
        if not rt60_opt:
            self.absc = UniformSampler(absc_opt)
        else:
            rt60_r = str2tuple(rt60_opt)
            self.rt60 = UniformSampler(rt60_r)
        dim_range = [str2tuple(t) for t in room_dim.split(";")]
        if len(dim_range) != 3:
            raise RuntimeError(f"Wrong format with --room-dim={room_dim}")
        self.dim_sampler = [UniformSampler(c) for c in dim_range]

    def generate(self, v=340):
        # (l, w, h)
        (l, w, h) = (s.sample() for s in self.dim_sampler)
        if self.rt60_opt:
            # no reflection is ok
            if self.rt60.max == 0:
                return Room(l, w, h, rt60=0)
            else:
                # check rt60 here
                S, V = l * w * h, (l * w + l * h + w * h) * 2
                # sabine formula
                rt60_min = 24 * V * np.log(10) / (v * S)
                if rt60_min >= self.rt60.max:
                    return None
                else:
                    rt60 = random.uniform(rt60_min, self.rt60.max)
                    return Room(l, w, h, rt60=rt60)
        else:
            absc = self.absc.sample()
            return Room(l, w, h, refl=np.sqrt(1 - absc))


class RirSimulator(object):
    """
    RIR simulator
    """
    def __init__(self, args):
        if args.gpu and not gpu_rir_available:
            raise RuntimeError("Please install gpuRIR first if --gpu=True")
        # make dump dir
        Path(args.dump_dir).mkdir(exist_ok=True, parents=True)
        self.rirs_cfg = []
        self.room_generator = RoomGenerator(args.rt60, args.abs_range,
                                            args.room_dim)
        self.mx, self.my = args.array_relx, args.array_rely
        self.array_topo = [str2tuple(t) for t in args.array_topo.split(";")]
        self.sr = args.sr
        self.args = args

    def _place_mic(self, room):
        x, y, _ = room.size
        # sample array location
        # (mx, my) center postion of array
        mx = random.uniform(*(x * v for v in self.mx))
        my = random.uniform(*(y * v for v in self.my))
        mz = random.uniform(*self.args.array_height)
        # place array
        room.set_mic(self.array_topo, (mx, my, mz))
        return (mx, my), room

    def _max_src_dist(self, rpos_2d, room_size_2d):
        mx, my = rpos_2d
        rx, ry = room_size_2d
        bound = [(0, 0), (0, ry), (rx, 0), (rx, ry)]
        dist = [((mx - x)**2 + (my - y)**2)**0.5 for (x, y) in bound]
        return max(dist)

    def _place_spk(self, center, room):
        num_rirs = self.args.num_rirs
        done, ntry = 0, 0
        mx, my = center
        rx, ry, rz = room.size
        max_retry = self.args.retry * num_rirs
        stats = []

        min_src_dist, max_src_dist = args.src_dist
        max_src_dist = min(max_src_dist, self._max_src_dist((mx, my),
                                                            (rx, ry)))

        Rf = lambda f: round(f, 3)
        while True:
            ntry += 1
            if ntry > max_retry:
                break
            sz = random.uniform(*args.speaker_height)
            if sz >= rz:
                continue

            # speaker distance
            dst = random.uniform(min_src_dist, max_src_dist)
            # sample from 0-360
            doa = random.uniform(0, np.pi * 2)

            sx = mx + np.cos(doa) * dst
            sy = my + np.sin(doa) * dst

            # check speaker location
            if 0 >= sx or sx >= rx or 0 >= sy or sy >= ry:
                continue
            done += 1
            stat = {
                "pos": (Rf(sx), Rf(sy), Rf(sz)),
                "doa": Rf(doa * 180 / np.pi),
                "dst": Rf(dst)
            }
            stats.append(stat)
            if done == num_rirs:
                break
        logger.info(f"Put speaker point: try/done = {ntry}/{done}")
        return done == num_rirs, stats

    def run_for_instance(self, room_id):
        room = None
        while not room:
            room = self.room_generator.generate(v=self.args.speed)
        rpos, room = self._place_mic(room)
        succ, scfg = self._place_spk(rpos, room)
        if succ:
            rcfg = room.conf()
            for idx, cfg in enumerate(scfg):
                cfg["loc"] = f"{self.args.dump_dir}/Room{room_id}-{idx + 1}.wav"
            if gpu_rir_available and self.args.gpu:
                # place all spakers and generate at once
                room.set_spk([cfg["pos"] for cfg in scfg])
                room.rir(f"{self.args.dump_dir}/Room{room_id}",
                         sr=self.sr,
                         rir_nsamps=int(self.sr * self.args.rir_dur),
                         v=self.args.speed,
                         gpu=True)
            else:
                for cfg in scfg:
                    # place one spk and generate rir one by one
                    room.set_spk(cfg["pos"])
                    room.rir(cfg["loc"],
                             sr=self.sr,
                             rir_nsamps=int(self.sr * self.args.rir_dur),
                             v=self.args.speed)
            # plot room
            room.plot(scfg,
                      f"{self.args.dump_dir}/Room{room_id}.{default_fmt}",
                      f"Room{room_id}")
            rcfg["spk"] = scfg
            self.rirs_cfg.append(rcfg)
        return succ

    def run(self):
        num_rooms = self.args.num_rooms
        max_retry = self.args.retry * num_rooms
        done, ntry = 0, 0
        while True:
            ntry += 1
            if ntry > max_retry:
                break
            succ = self.run_for_instance(done + 1)
            if succ:
                done += 1
            if done == num_rooms:
                break
        # dump rir configurations
        with open(Path(args.dump_dir) / "rir.json", "w") as f:
            json.dump(self.rirs_cfg, f, indent=2)
        logger.info(f"Generate {self.args.num_rirs * num_rooms:d} rirs, " +
                    f"{done:d} rooms done, try = {ntry}")


def run(args):
    simulator = RirSimulator(args)
    simulator.run()


"""
Run egs:
$cmd JOB=1:$nj ./exp/rir_simu/rir_generate_2d.JOB.log \
  ./scripts/sptk/rir_generate_2d.py \
    --num-rirs $num_rirs \
    --dump-dir $dump_dir/JOB \
    --array-height "1.2,1.8" \
    --room-dim "4,10;4,10;2,4" \
    --rt60 "0.2,0.5" \
    --array-relx "0.3,0.7" \
    --array-rely "0.3,0.7" \
    --speaker-height "1,2" \
    --source-distance "1,4" \
    --rir-dur 0.5 \
    --dump-cfg true \
    $num_room
"""
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Command to generate single/multi-channel RIRs "
        "(using rir-simulate or pyrirgen from https://github.com/Marvin182/rir-generator). "
        "In this command, we will simulate several rirs for each room, which is "
        "configured using --num-rirs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("num_rooms",
                        type=int,
                        help="Total number of rooms to simulate")
    parser.add_argument("--num-rirs",
                        type=int,
                        default=40,
                        help="Number of rirs to simulate for each room")
    parser.add_argument("--dump-cfg",
                        type=strtobool,
                        default=True,
                        help="If true, dump rir configures out in json format")
    parser.add_argument("--rir-dur",
                        type=float,
                        default=0.5,
                        help="Duration of the simulated rir (s)")
    parser.add_argument("--sr",
                        type=int,
                        default=16000,
                        help="Sample rate of simulated signal")
    parser.add_argument("--dump-dir",
                        type=str,
                        default="rir",
                        help="Directory to dump generated rirs")
    parser.add_argument("--room-dim",
                        type=str,
                        default="7,10;7,10;3,4",
                        help="Constraint for room length/width/height, "
                        "separated by semicolon")
    parser.add_argument("--array-height",
                        type=str2tuple,
                        default=(1, 2),
                        help="Range of array's height")
    parser.add_argument("--array-relx",
                        type=str2tuple,
                        default=(0.4, 0.6),
                        help="Area of room to place microphone array randomly"
                        "(relative values to room's length)")
    parser.add_argument("--array-rely",
                        type=str2tuple,
                        default=(0.4, 0.6),
                        help="Area of room to place microphone array randomly"
                        "(relative values to room's width)")
    parser.add_argument("--speaker-height",
                        type=str2tuple,
                        default=(1.6, 2),
                        help="Range of speaker's height")
    parser.add_argument("--array-topo",
                        type=str,
                        default="0,0.05;0.05,0;0,-0.05;-0.05,0",
                        help="Topology of the 2D microphone arrays.")
    parser.add_argument("--absorption-coefficient-range",
                        type=str2tuple,
                        dest="abs_range",
                        default=(0.2, 0.8),
                        help="Range of absorption coefficient "
                        "of the room material. Absorption coefficient "
                        "is located between 0 and 1, if a material "
                        "offers no reflection, the absorption "
                        "coefficient is close to 1.")
    parser.add_argument("--rt60",
                        type=str,
                        default="0.2,0.7",
                        help="Range of RT60, this option "
                        "has higher priority than "
                        "--absorption-coefficient-range")
    parser.add_argument("--sound-speed",
                        type=float,
                        dest="speed",
                        default=343,
                        help="Speed of sound")
    parser.add_argument("--retry",
                        type=int,
                        default=5,
                        help="Max number of times tried to generate rirs "
                        "for a specific room (retry * num_rirs)")
    parser.add_argument("--source-distance",
                        type=str2tuple,
                        dest="src_dist",
                        default=(1, 3),
                        help="Range of distance between "
                        "microphone arrays and speakers")
    parser.add_argument("--gpu",
                        type=strtobool,
                        default=False,
                        help="Use gpuRIR from "
                        "https://github.com/DavidDiazGuerra/gpuRIR.git")
    args = parser.parse_args()
    run(args)
