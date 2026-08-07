# Releasing

There is **one codebase and two ways to install it**. Not two products.

| | Command | Platform | Who |
|---|---|---|---|
| **Source** | `git clone` + `run-portable.bat` / `python app.py` | Windows, macOS, Linux | developers, tinkerers |
| **Packaged** | download `MarketForge-win64.zip`, unzip, double-click | **Windows only** | everyone else |

The packaged app is this repo, frozen with PyInstaller. Same code, same MIT
licence, same features. It is a build artifact, not a fork.

**Windows-only is a PyInstaller property, not a decision.** It builds for the OS
it runs on. A macOS app means building on macOS (a CI runner is enough). Nobody
is locked out in the meantime, because the source path already works everywhere.

## The binary never goes in git

Committed binaries live in history forever and cannot be removed without a
rewrite. They belong on **GitHub Releases**, which is also what gives people a
stable download URL and a changelog.

## Cutting a release

1. Everything merged to `main`, working tree clean.
2. Tag it. The tag is the version:
   ```
   git tag -a v0.2.0 -m "Packaged Windows app"
   git push origin v0.2.0
   ```
3. The workflow in `.github/workflows/release.yml` builds on a Windows runner and
   attaches `MarketForge-win64.zip` to the release.
4. Point the website's download at the release, or at
   `/releases/latest` so it never needs updating again.

## Before every release, by hand

Automation cannot check these, and each one has bitten this repo.

- [ ] **No `bot/.env` in the build.** Grep the zip. Keys must never ship.
- [ ] **Ships paper-first.** `bot/.env.template` has `STOCK_ENV=paper` and both
      auto flags false.
- [ ] **The exit guarantee survived.** `bot/src/api.py` still has the inline poll,
      the disk-backed watcher, and the 30s sweep. A packaged app that can enter a
      position without arming an exit is worse than no app.
- [ ] **Tested on a machine with no Python and no repo.** "Works on the build
      machine" proves nothing about a frozen build.
- [ ] Version bumped in the release notes with what actually changed.

## Versioning

Plain semver. `0.x` while the exit guarantee has not been proven on a real slow
fill. `1.0` is not a feature milestone, it is the point where the safety story is
demonstrated rather than argued.
