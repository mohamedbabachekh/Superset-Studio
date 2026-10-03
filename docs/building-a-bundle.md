# Building a deployable bundle

A bundle is **one tar** that installs the whole kit on a machine with Docker and no internet: the image, its two
dependencies, the compose file, the scripts, the documentation and an installer. This is how to build one that
will not embarrass you on the target.

---

## 1 · Before you build

| | |
|---|---|
| Docker running | `docker info` answers |
| Disk | ~2 GB free: the save is ~565 MB and it is written twice (stage, then tar) |
| Network | only for the **first** build on a machine, to pull Superset, PostgreSQL and Redis; after that it builds offline |
| The repository | clean enough that you know what you are shipping — `git status` |

And one habit worth keeping: **run the kit before you bundle it.** A bundle is a photograph of an image that was
never asked to work.

```bash
bash install.sh            # it comes up, you sign in, SQL Lab answers
bash install.sh --stop
```

---

## 2 · Build it

```bash
bash build-devkit-bundle.sh
```

→ `dist/sharpsight-superset-devkit-<today>.tar.gz` and its `.sha256`.

| Variable | Default | Use it when |
|---|---|---|
| `VERSION` | today's date (`20261003`) | you build **twice in one day** — see the warning below |
| `OUT` | `./dist` | you want it straight onto a USB stick or a share |
| `INCLUDE_CEM` | `0` | you want the Yacht driver (and, if present, your filled `cem.env`) inside |
| `DEVKIT_WITH_JDBC` | `1` | `0` builds a smaller image with no Java and no CEM helper |

```bash
INCLUDE_CEM=1 VERSION=20261003b bash build-devkit-bundle.sh
```

> **Never build twice under the same name.** Two tarballs called `…-20261003.tar.gz` with different contents is
> the single most expensive mistake here: someone copies the old one, verifies it against the new checksum, and
> spends an hour on a "corrupt download". Bump `VERSION` — `20261003b`, `20261003c` — every time you rebuild on
> the same day.

**`INCLUDE_CEM=1` and secrets.** It always carries the driver jar from `driver-jars/`. It carries `cem.env`
**only if that file is non-empty**, it says so out loud, and the manifest then reads `contains_secrets=yes`.
Such a tarball holds an account and its password: treat it like a password file — not on a shared drive, not
attached to a ticket. Without a `cem.env`, the kit installs with `cem.env.example`, which names everything but
the secrets.

---

## 3 · Verify it — before it leaves the machine

```bash
bash verify-bundle.sh dist/sharpsight-superset-devkit-20261003.tar.gz
```

It checks the checksum (and that the checksum file names *this* tarball), that every file `install.sh` needs is
inside, that the images archive is whole rather than truncated, that the manifest tells the truth about secrets,
that no `.env` or mart dump slipped in, and that every shell script inside is **LF** — a CRLF `install.sh` does
not execute on Linux at all.

For a full rehearsal — unpack into a scratch folder, install, and sign in:

```bash
INSTALL=1 bash verify-bundle.sh dist/sharpsight-superset-devkit-20261003.tar.gz
```

> `INSTALL=1` **removes the kit currently on this machine** (containers and volumes) before installing the
> bundle's own. Do not run it on a machine whose kit holds work you want.

---

## 4 · Ship it

Copy **both** files — the tarball and its `.sha256`.

On the target, **stand in the folder that holds them** before checking: the checksum file names the tarball
without a path, so `sha256sum -c` looks for it in the current directory.

```bash
cd /where/both/files/are
sha256sum -c sharpsight-superset-devkit-20261003.tar.gz.sha256
```

If the target is Linux reading the files from a Windows drive (`/mnt/c/...` under WSL), **copy them into the
Linux filesystem first**: `install.sh` writes `.env` readable only by you, and compose bind-mounts folders from
where it stands — neither survives a `drvfs` mount intact, and everything is far slower.

---

## 5 · Install, or upgrade, on the target

**A new machine:**

```bash
tar -xzf sharpsight-superset-devkit-20261003.tar.gz
cd sharpsight-superset-devkit-20261003
bash install.sh
```

It generates its own secrets and prints the admin password **once** — copy it; it is also in `.env`.

**A machine that already runs a kit**, keeping its dashboards, extracts and settings:

```bash
cd ~/<the old folder> && bash install.sh --stop
cp ~/<the old folder>/cem.env ~/cem.env.backup        # keep a copy before anything
tar -xzf sharpsight-superset-devkit-20261003.tar.gz
cp ~/<the old folder>/.env ~/<the old folder>/cem.env sharpsight-superset-devkit-20261003/
cd sharpsight-superset-devkit-20261003 && bash install.sh
```

Carrying `.env` across is not optional: the database volume survives, and its password lives in that file. Start
without it and the kit refuses with *"the database already on this machine does not accept the password in this
.env"* — which is the guard doing its job, not a fault.

A good install says, in this order: `loading images (this takes a minute) …`, `cem.env: N setting(s) given to
the container` (or that the CEM is not configured), `ready — http://…`, and then what is already connected.

> **`loading images` must appear on an upgrade.** The image tag never changes between versions, so `install.sh`
> remembers the checksum of the images archive it loaded; if it says nothing about loading, you are still
> running the old image.

---

## 6 · Afterwards

- **Keep the previous bundle** until the new one is installed and used. Retire it after, never in the same
  breath as building its replacement.
- **Write down what changed** in [CHANGELOG.md](../CHANGELOG.md), with the version and the checksum.
- If the kit must also carry the platform's dashboards, that is a second, separate archive — see
  [connecting-sharpsight.md](connecting-sharpsight.md). It never goes in the bundle: it holds customer data.

---

## What goes wrong, and what it looks like

| Symptom | Cause |
|---|---|
| `sha256sum: …tar.gz: No such file or directory` | you ran `-c` from another folder — `cd` to where both files are |
| checksum mismatch on a file that copied fine | two builds share a name; rebuild with a new `VERSION` |
| `bash: install.sh: /bin/bash^M: bad interpreter` | CRLF got in — the repository's `.gitattributes` prevents it; `verify-bundle.sh` catches it |
| the install hangs at `loading images` | it is 565 MB being unpacked; on a slow disk give it a few minutes |
| upgrade installs but behaves like the old version | the images were not reloaded (see §5) |
| `FileNotFoundError: C:/Users/.../Temp/…zip` while scripting on Windows | Git Bash rewrote a container path; put it inside a `sh -c '…'` string |
| the build says `INCLUDE_CEM=1 but driver-jars/ holds no .jar` | the Yacht driver is not in `driver-jars/`; the kit will install without it |
