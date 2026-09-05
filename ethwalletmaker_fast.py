#!/usr/bin/env python3
"""
Vanity Etherium Wallet Maker - parallel edition.
Based on ethwalletmaker.py by thelamewizard.

Same idea as the original: generate random keypairs until the address starts
with the characters you want. What changed:
  - every CPU core searches at once instead of just one
  - nothing is printed inside the hot loop (that was the bigger bottleneck)
  - one progress line per second instead of three lines per attempt

Memory: each worker is a separate Python interpreter and sits at a flat ~15 MB
once it is running, so the footprint is just 15 MB x workers. Cap it with
--workers, or state a budget with --max-mem and let it pick the worker count.

Usage:
    python ethwalletmaker_fast.py                  # prompts for the prefix
    python ethwalletmaker_fast.py dead             # prefix on the command line
    python ethwalletmaker_fast.py dead 8           # ...and only use 8 workers
    python ethwalletmaker_fast.py dead --max-mem 200
"""

import argparse
import math
import multiprocessing as mp
import os
import signal
import sys
import time
from secrets import token_bytes

from coincurve import PublicKey
from Crypto.Hash import keccak

VALID = set("0123456789abcdef")
BATCH = 2000  # attempts between shared-counter updates and stop checks
MB_PER_WORKER = 15  # measured steady-state RSS of one worker; it does not grow
PARENT_MB = 25  # the coordinator plus multiprocessing's resource tracker
SPARE_CORES = 2  # left for the OS so the machine stays usable while searching
DEFAULT_MAX_MEM_MB = 100 * 1024  # 100 GB
SAFE_FRACTION = 0.8  # never budget more than this share of installed RAM


def keccak_256(data):
    return keccak.new(digest_bits=256, data=data)


def search(prefix, found, result_q, counter, parent_pid):
    """One worker. Loops until it hits, until another worker does, or until the
    parent goes away."""
    # local binds: attribute lookups add up over billions of iterations
    _token_bytes = token_bytes
    _from_secret = PublicKey.from_valid_secret
    _keccak = keccak.new
    n = len(prefix)
    try:
        while not found.is_set():
            # If the parent died without running its cleanup (kill -9, crash,
            # closed terminal) we get reparented to launchd. Without this check
            # a worker keeps a core pegged and its ~15 MB resident forever, and
            # every abandoned run stacks another set on top of the last.
            if os.getppid() != parent_pid:
                return
            done = 0
            hit = None
            for _ in range(BATCH):
                done += 1
                private_key = _token_bytes(32)
                try:
                    public_key = _from_secret(private_key).format(compressed=False)[1:]
                except ValueError:
                    continue  # astronomically rare: key landed >= the curve order
                addr = _keccak(digest_bits=256, data=public_key).digest()[-20:].hex()
                if addr[:n] == prefix:
                    hit = (private_key.hex(), addr)
                    break
            with counter.get_lock():
                counter.value += done
            if hit is not None:
                result_q.put(hit)
                found.set()
                return
    except KeyboardInterrupt:
        return


def shutdown(procs, found):
    """Escalate until every worker is actually gone. Safe to call twice."""
    found.set()
    for stage in ("join", "terminate", "kill"):
        alive = [p for p in procs if p.is_alive()]
        if not alive:
            return
        for p in alive:
            if stage == "terminate":
                p.terminate()
            elif stage == "kill":
                p.kill()
        for p in alive:
            p.join(timeout=2)


def installed_mb():
    """Physical RAM in MB, or None if we cannot tell."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return None


def fmt_mem(mb):
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def parse_mem(text):
    """Accept 500, 500mb, 8g, 100GB - all returned as MB."""
    t = text.strip().lower().replace("b", "")
    mult = 1
    if t.endswith("g"):
        mult, t = 1024, t[:-1]
    elif t.endswith("m"):
        t = t[:-1]
    try:
        value = float(t) * mult
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a memory size: {text!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError("memory budget must be positive")
    return int(value)


def memory_budget(requested):
    """The budget we will honour, plus the installed total for context."""
    installed = installed_mb()
    budget = requested if requested is not None else DEFAULT_MAX_MEM_MB
    capped_by_ram = False
    if installed is not None and budget > installed * SAFE_FRACTION:
        budget = int(installed * SAFE_FRACTION)
        capped_by_ram = True
    return budget, installed, capped_by_ram


def human_time(seconds):
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def check_prefix(prefix):
    prefix = prefix.strip().lower()
    if prefix.startswith("0x"):
        prefix = prefix[2:]
    bad = sorted(set(prefix) - VALID)
    if bad:
        sys.exit(f"Invalid character(s) in prefix: {' '.join(bad)}. Use only 0-9 and a-f.")
    if not prefix:
        sys.exit("Empty prefix - nothing to search for.")
    if len(prefix) > 12:
        sys.exit(f"A {len(prefix)}-character prefix needs ~16^{len(prefix)} tries. Not happening.")
    return prefix


def parse_args():
    p = argparse.ArgumentParser(
        description="Search for an Etherium address with a chosen prefix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prefix", nargs="?", help="leading hex characters to search for")
    p.add_argument("workers", nargs="?", type=int, help="number of worker processes")
    p.add_argument("-w", "--workers", dest="workers_flag", type=int, metavar="N",
                   help="number of worker processes (same as the positional form)")
    p.add_argument("--max-mem", type=parse_mem, metavar="SIZE",
                   help=f"memory budget, e.g. 100gb / 8g / 500mb / 500 "
                        f"(default {fmt_mem(DEFAULT_MAX_MEM_MB)}, reduced to "
                        f"{SAFE_FRACTION:.0%} of RAM on smaller machines)".replace("%", "%%"))
    args = p.parse_args()
    if args.workers_flag is not None:
        args.workers = args.workers_flag
    return args


def pick_workers(requested, budget_mb):
    """Cores decide the default; the memory budget is the hard ceiling."""
    cores = os.cpu_count() or 1
    if requested is None:
        workers = max(1, cores - SPARE_CORES)  # leave the OS some room
        limited_by = "leaving %d of %d cores free" % (SPARE_CORES, cores)
    else:
        workers = max(1, requested)  # an explicit count may oversubscribe cores
        limited_by = "you asked for %d" % requested
    affordable = max(1, (budget_mb - PARENT_MB) // MB_PER_WORKER)
    if affordable < workers:
        workers = affordable
        limited_by = "capped by the %s memory budget" % fmt_mem(budget_mb)
    return workers, limited_by


def main():
    args = parse_args()
    print("Vanity Etherium Wallet Maker (parallel).\nby: thelamewizard")
    prefix = check_prefix(args.prefix if args.prefix else input(
        "Only characters 0-9 and lower-case a-f are valid.\n"
        "Enter your chosen leading characters:\n"
    ))

    budget, installed, capped_by_ram = memory_budget(args.max_mem)
    workers, limited_by = pick_workers(args.workers, budget)
    footprint = workers * MB_PER_WORKER + PARENT_MB
    space = 16 ** len(prefix)

    line = f"\nMemory budget: {fmt_mem(budget)}"
    if installed is not None:
        line += f" of {fmt_mem(installed)} installed"
        if capped_by_ram:
            line += f" (asked for {fmt_mem(args.max_mem or DEFAULT_MAX_MEM_MB)}, "
            line += f"trimmed to {SAFE_FRACTION:.0%} of RAM)"
    print(line)
    print(f"Workers: {workers} using ~{fmt_mem(footprint)}"
          f" ({footprint / budget:.1%} of budget) - {limited_by}")

    print(f"\nSearching for 0x{prefix}...")
    print(f"Expected attempts: {space:,}   (Ctrl+C to give up)\n")

    found = mp.Event()
    result_q = mp.Queue()
    counter = mp.Value("Q", 0)

    procs = [
        mp.Process(target=search,
                   args=(prefix, found, result_q, counter, os.getpid()),
                   daemon=True)
        for _ in range(workers)
    ]

    # SIGTERM and SIGHUP (closed terminal) take the same path as Ctrl+C so the
    # workers get cleaned up instead of being orphaned.
    def stop(signum, frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    start = time.perf_counter()
    for p in procs:
        p.start()

    try:
        while not found.is_set():
            time.sleep(1.0)
            elapsed = time.perf_counter() - start
            tried = counter.value
            rate = tried / elapsed if elapsed else 0
            # geometric process: report the odds, not a fake countdown
            odds = 1 - math.exp(-tried / space) if space else 1
            line = (
                f"\r{tried:>15,} tried | {rate:>10,.0f}/s | {odds:5.1%} covered"
            )
            if rate:
                line += (
                    f" | 50% by {human_time(math.log(2) * space / rate)}"
                    f" | 90% by {human_time(math.log(10) * space / rate)}"
                )
            sys.stdout.write(line + "   ")
            sys.stdout.flush()

        private_key, addr = result_q.get()
        elapsed = time.perf_counter() - start
        tried = counter.value

        print(f"\r{' ' * 100}\r", end="")
        print(f"{tried:,} wallets itterated through in {human_time(elapsed)} "
              f"({tried / elapsed:,.0f}/s) to find your chosen one\n")
        print("Private Key:", private_key)
        print("Public ETH Address: 0x" + addr)
    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        shutdown(procs, found)


if __name__ == "__main__":
    mp.freeze_support()
    main()
