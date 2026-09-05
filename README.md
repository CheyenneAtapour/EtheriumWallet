# EtheriumWallet

Simple Vanity Wallet maker that can be run locally.

Generates random keypairs until it finds an Etherium address starting with the
characters you chose. There are two versions:

| script | what it does |
| --- | --- |
| `ethwalletmaker.py` | the original, single core |
| `ethwalletmaker_fast.py` | searches on every core at once, ~14x faster |

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

That installs [coincurve](https://pypi.org/project/coincurve/) for the elliptic
curve maths and [pycryptodome](https://pypi.org/project/pycryptodome/) for
keccak-256.

## Running it

The parallel version, which is the one you probably want:

```sh
./run_parallel.sh              # prompts for the prefix
./run_parallel.sh dead         # prefix on the command line
```

The original single-core version:

```sh
./run.sh
```

Only `0-9` and lower-case `a-f` are valid characters. A leading `0x` is fine,
it gets stripped. Press Ctrl+C to give up.

While it runs you get one progress line per second:

```
     18,842,000 tried |    981,654/s |  0.4% covered | 50% by 50.5m | 90% by 2.8h
```

"50% by" is the time by which there is an even chance of a hit. It is not a
countdown — each attempt is independent, so you can get lucky in a second or
run for hours past the estimate.

### How long it takes

Each extra character is 16x more work. The table below assumes ~1M addresses a
second, which is what 16 workers managed on the machine this was written on.
Your rate is printed live, so scale accordingly.

| prefix length | expected attempts | ballpark |
| --- | --- | --- |
| 4 | 65 thousand | instant |
| 5 | 1 million | about a second |
| 6 | 17 million | under 20 seconds |
| 7 | 268 million | ~5 minutes |
| 8 | 4.3 billion | ~1.2 hours |
| 9 | 69 billion | most of a day |
| 10 | 1.1 trillion | ~2 weeks |
| 11 | 18 trillion | ~7 months |
| 12 | 281 trillion | ~9 years |

Anything over 12 characters is refused.

These are averages, not deadlines. Each attempt is independent, so half the
time you will finish sooner and half the time later, sometimes much later.

## Controlling CPU and memory

At startup it prints the memory budget and what it is actually going to use:

```
Memory budget: 100.0 GB of 128.0 GB installed
Workers: 22 using ~355 MB (0.35% of budget) - leaving 2 of 24 cores free
```

The default budget is **100 GB**. On a machine with less RAM than that it is
trimmed to 80% of what is installed, and the line says so:

```
Memory budget: 19.2 GB of 24.0 GB installed (asked for 100.0 GB, trimmed to 80% of RAM)
```

Worth knowing: a worker only costs ~15 MB, so 100 GB is room for roughly 6,800
of them. Your core count runs out long before the budget does, and the trailing
part of the "Workers:" line tells you which limit is actually biting. The
budget is a backstop, not the usual control.

### Choosing the worker count

```sh
./run_parallel.sh dead 8            # 8 workers
./run_parallel.sh dead --workers 8  # same thing
```

The default is every core but two, so the machine stays usable. An explicit
count is allowed to exceed your core count if you want to oversubscribe; the
memory budget is then the only ceiling.

### Choosing a memory budget

`--max-mem` takes plain MB or a unit suffix:

```sh
./run_parallel.sh dead --max-mem 500      # 500 MB
./run_parallel.sh dead --max-mem 500mb
./run_parallel.sh dead --max-mem 8g
./run_parallel.sh dead --max-mem 100gb
```

When the budget is small enough to bite, it wins over the core count:

```
$ ./run_parallel.sh dead --max-mem 100mb
Memory budget: 100 MB of 24.0 GB installed
Workers: 5 using ~100 MB (100.0% of budget) - capped by the 100 MB memory budget
```

Fewer workers means proportionally slower searching, nothing else.

### Memory behaviour

Each worker is its own Python process holding a flat ~15 MB, plus ~25 MB for
the coordinator. That figure does not grow, however long you leave it running.

Workers shut themselves down if the parent process goes away, so killing the
run - Ctrl+C, `kill`, closing the terminal, even `kill -9` - never leaves
strays behind burning CPU and memory. If you are ever unsure:

```sh
pgrep -f ethwalletmaker_fast
```

## Security

The private key is printed to your terminal and never written to disk or sent
anywhere. It will be sitting in your shell scrollback, so move it somewhere
safe and clear the scrollback if the wallet is going to hold anything.
