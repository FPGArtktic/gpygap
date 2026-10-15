# Running the online side on Windows

The online machine does not have to be Linux. Running it on Windows costs a little
setup and buys the one thing a Linux host cannot do: **it builds the Windows wheels
itself**, instead of publishing them as gaps for somebody else to fill.

!!! warning "Tested by hand, not by CI"
    Every CI job runs the Linux host. This page is supported and the pieces it uses are
    the same ones CI exercises, but the Windows wrapper itself is checked manually. If
    you run this arrangement and something does not match, that is worth an issue.

## What changes, and what does not

Almost nothing changes. The mirror is built inside the same Linux container image, by the
same code, producing the same `simple/` index. What differs is outside the container:

```mermaid
flowchart LR
    subgraph host["Windows online machine"]
        PS["bin/gpygap.ps1"] --> ENG["Docker Desktop<br/>(Linux containers, WSL2)"]
        ENG --> G["gpygap container<br/>C:\\gpygap mounted at /work"]
        G -->|"engine socket"| ENG
        ENG --> ML["manylinux / musllinux<br/>sibling containers"]
        WIN["python -m pip wheel<br/>natively on the host"] --> INC["incoming-wheels\\"]
        INC -.->|"build.external_dir"| G
    end
    G --> M["C:\\gpygap\\gpygap<br/>the mirror"]
```

| | Linux host | Windows host |
| --- | --- | --- |
| Wrapper | `bin/gpygap` (bash, podman) | `bin/gpygap.ps1` (PowerShell, Docker Desktop) |
| Project path inside | the same absolute path as outside | `/work` |
| Sibling container paths | identity | translated via `build.container_root` / `build.host_root` |
| Linux wheels | built in manylinux/musllinux containers | the same, through the WSL2 backend |
| Windows wheels | a gap, or adopted from `build.external_dir` | **built on this machine**, then adopted |

## Why the path translation is needed

On Linux the wrapper mounts the project directory at the same absolute path inside the
container as outside, so a sibling container can mount the same host path and every
mirror-relative path still resolves. On Windows that trick is unavailable: the mirror is
`/work/gpygap` inside and `C:\gpygap\gpygap` outside, and the engine — which creates the
sibling — only understands the second one.

So `gpygap.toml` carries the mapping:

```toml
[build]
container_root = "/work"
host_root = "C:\\gpygap"
```

Both or neither; the configuration refuses one without the other. With them set, gpygap
rewrites the `--volume` arguments of its builder containers and leaves the paths inside
alone.

## Setting it up

1. **Docker Desktop** with the WSL2 backend, in *Linux containers* mode (the default).
   Podman Desktop works the same way. Windows containers are not used and not needed.
2. **Clone the repository** somewhere without spaces in the path, for example
   `C:\gpygap`.
3. **Edit `gpygap.toml`**: add the `container_root` / `host_root` pair above, and point
   `build.external_dir` at a directory for the wheels this machine builds natively:
   ```toml
   [build]
   external_dir = "incoming-wheels"
   container_root = "/work"
   host_root = "C:\\gpygap"
   ```
4. **Run it**:
   ```powershell
   .\bin\gpygap.ps1 sync --config gpygap.toml
   ```

## Building the Windows wheels on the same machine

The first run publishes `MIRROR-GAPS.json`: every `(package, platform, Python version)`
still without a wheel. On a Windows host those are exactly the ones this machine can
build, with the MSVC Build Tools installed:

```powershell
python .\.github\ci\build-windows-wheels.py .\gpygap .\incoming-wheels
.\bin\gpygap.ps1 sync --config gpygap.toml      # adopts them
```

Run it once per Python version in `targets.python` — `py -3.12`, `py -3.13` and so on —
because a compiled extension's wheel serves one interpreter. A pure-Python package needs
only one run: its `py3-none-any` wheel covers every target, Linux included.

Two runs rather than one is not an accident of the tooling: it is the same shape as a
monthly cycle, where one run publishes what is missing and the next folds in what was
built in between.

## Can one Windows machine do the whole job?

Yes, and it is the only single-machine arrangement that can.

| | Linux host | Windows host |
| --- | --- | --- |
| manylinux / musllinux wheels | native containers | Linux containers through the WSL2 backend |
| `win_amd64` wheels | **impossible** | native, under MSVC |
| `py3-none-any` wheels | yes, and they cover every target | same |
| macOS wheels | impossible | impossible |

A Linux host cannot produce a Windows wheel at all, so it always ends a run with gaps
somebody else has to fill. A Windows host with Docker Desktop in Linux-container mode has
both toolchains: the Linux wheels come out of the same pypa images, and the Windows
wheels come out of MSVC on the host.

It is still two phases rather than one command, and that is structural rather than a
missing feature. gpygap itself runs inside a Linux container, so it cannot reach MSVC on
the host; the host builds the Windows wheels separately and `build.external_dir` joins
them on the next run. Making it one pass would mean running gpygap natively on Windows
and giving `WheelBuilder` a backend that shells out to the host compiler — a design
change, not a setting.

## What CI can and cannot check here

The `mirror-windows` job runs gpygap natively on a Windows runner and installs from what
it produced, offline. That covers the part most likely to break and least likely to be
noticed: resolution, selection, download, the `simple/` index, checksums and offline
verification, all over Windows paths and `file://` URLs.

It stops there. A GitHub-hosted Windows runner has Docker in Windows-container mode and
no Docker Desktop, so there is no Linux container to build an sdist in — and building one
outside a container means running code from the internet on the host, which this project
does not do anywhere. Testing the full Windows-host arrangement needs a self-hosted
runner with Docker Desktop and the WSL2 backend.

## What is still not possible

macOS. Neither host builds for it, for the same reason Linux does not build for Windows,
and no amount of configuration changes that. If your developers use macOS, add
`macos-arm64` to `targets.platforms` to mirror the wheels that exist upstream, and expect
`MIRROR-GAPS.json` to name the rest.
