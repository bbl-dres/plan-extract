# Reading DWG files: candidates, licences, recommendation

*Research note, 15 October 2026. Context: `pilot/v2-pipeline/scripts/fpx/inputs.py` reads DXF with ezdxf and DWG through a swappable `DwgConverter` (DWG in, DXF out, run as a subprocess with a timeout); [pipeline.md §9](../pipeline.md#9-open-questions) leaves the converter and its licence open. The app is MIT, runs on a public Ubuntu VPS (Python 3.14, systemd, nginx), and uploads must not leave the server.*

**Summary.** Only two open-source code bases read modern DWG (R2004–R2018) well enough to matter: GNU LibreDWG (C, GPL-3.0-or-later) and, since February 2026, acadrust (pure Rust, MPL-2.0). Everything else is either a wrapper of one of them (libredwg-web, libredwg-converter, cadcraft), an incomplete reader (libdxfrw, ezdwg, IxMilia.Dwg), a .NET library without a CLI (ACadSharp, MIT), proprietary with terms that exclude a public service for non-members (ODA File Converter), paid (QCAD Professional, ODA membership) or cloud (Autodesk APS, plans leave the server). Tested here on the two public test DWGs of plan-check (AutoCAD 2004 and 2018 formats), LibreDWG read every entity type the pipeline needs (lines, polylines, arcs, circles, hatches, inserts, text, mtext, layers, INSUNITS) in under a second and wrote a DXF that ezdxf loads; ezdwg lost most entities of the 2004 file; acadrust could not be built on this machine. **Recommendation: LibreDWG `dwg2dxf` 0.14 on the server, built from the GNU tarball in `setup.sh` and run sandboxed as the separate process the existing `LibreDwgConverter` already expects (GPL stays outside the MIT app); evaluate acadrust on the BBL corpus as the permissive, memory-safe successor and as the only clean route to a browser preview; no browser WASM preview for now; avoid the ODA File Converter on the public server and any cloud conversion.**

## Table 1: candidates

| Candidate | Type / language | Licence (verified file) | DWG versions read | Output | Runs on | Activity (15 Oct 2026) | Fit |
|---|---|---|---|---|---|---|---|
| LibreDWG `dwg2dxf` [1–6] | CLI converter + C library | GPL-3.0-or-later (COPYING) | "all DWG versions"; "some very advanced R2010+ objects fail to read and are skipped"; dwg2dxf "about 90% coverage" | DXF r12–r2018 (`--as rNNNN`), JSON, SVG; DWG write up to R2004 | Linux: build from source (no Ubuntu/Debian package [7]; conda-forge is 0.11 of 2025 [8]; openSUSE ships 0.14) | 0.14 on 2026-08-31, nightlies 12–13 Oct 2026, 1.6k stars, 95 open issues, long CVE history [9] | **Primary (server)** |
| libredwg-web `@mlightcad/libredwg-web` [10–11] | WASM build of LibreDWG + TS API | GPL-3.0 (package.json) | as LibreDWG | `DwgDatabase` JSON, DXF bytes (`dwg_write_dxf`), SVG | Browser and Node; wasm 10.8 MB | 0.8.0 on 2026-10-13, first publish 2025-03, 81 stars | Browser preview only, GPL in the bundle |
| acadrust [13] | Rust library (pure Rust) | MPL-2.0 (LICENSE) | R12–R2018 read and write per README; 48 entity types; failsafe read for DXF (DWG not stated) | DXF R12–R2018, DWG | Native Linux; `wasm32-unknown-unknown` build-checked in CI | 0.6.4 on 2026-10-11, 33 releases since 2026-02-07, 118 stars, 754 commits, 14 open issues, one maintainer | **Candidate to test** |
| cadcraft [14] | Desktop CAD app + `cadcraft-cli convert`, Rust | MIT OR Apache-2.0 (NOTICE); DWG via acadrust 0.6.4 "used unmodified" | R13–2018 read, R2000–2018 write, all through acadrust; **web build: "DWG files can't be opened in the web build yet"** (`crates/dwg/src/lib.rs`) | DXF bytes (acadrust writer + header vars) | Linux/Windows/macOS native; CLI archive shipped for macOS only in v0.7.0 | v0.1.0 on 2026-10-08 to v0.7.0 on 2026-10-14; 4.9k stars, 266 open issues, "early, fast development" | Wrapper only; use acadrust directly |
| ezdwg [15] | Rust core + Python API | MIT | R13–R2018 (R13/R14 experimental); native decode for R2007+ limited to LINE/ARC/LWPOLYLINE/POINT/CIRCLE/ELLIPSE, text from string stream | DXF via `ezdwg.to_dxf` (ezdxf backend) | Linux/Windows/macOS wheels | 0.16.0 on 2026-10-14, 25 stars | No: incomplete on the BBL 2004 file (test below) |
| ACadSharp [16] | .NET library (netstandard2.0/2.1, net8–10) | MIT (LICENSE) | DWG read AC1014–AC1032; write all but AC1021 | DXF ASCII/binary, DWG | Linux with the .NET runtime; no CLI; no Blazor/WASM build found | 3.8.0 on 2026-09-21, 885 stars, 1.7M NuGet downloads | Fallback: needs .NET plus a small own CLI |
| libdxfrw 2.0.0 (+ `@mlightcad/libdxfrw-web`) [17] | C++ library + `dwg2dxf` CLI; WASM port | GPL-2.0-or-later (COPYING); web port GPL-2.0-only | DWG read "experimental", versions not listed (web port: R14–2020) | DXF | Linux; browser | 294 stars; FreeCAD uses its dwg2dxf | No: reader experimental |
| IxMilia.Dwg [18] | .NET library | MIT | not stated (README links the R13–R14 spec) | – | – | 58 stars | No |
| ODA File Converter (+ `ezdxf.addons.odafc`) [19–23] | Qt CLI/GUI converter | Proprietary freeware, ODA Community User Agreement of 2025-09-05 | R12–R2018 in; ACAD9–ACAD2018 out | DXF/DWG any version | Linux deb/rpm/AppImage (needs `xvfb` headless) | current; older versions only for members | Best fidelity, but terms exclude a public service (Table 3) |
| ODA Drawings SDK (membership) [22] | C++/.NET SDK | Proprietary; Commercial $3,000 first year / $2,250 renewal (limited: desktop, <100 copies), Sustaining $7,500 / $4,500 (unlimited, web/SaaS), Founding $37,500 / $18,000 (source) | all | any | Linux | – | No for an MIT pilot |
| QCAD Professional `dwg2dwg` [24] | CLI converter (proprietary, Qt) | Proprietary; Server Licence CHF 479.96 per server, perpetual, one year of updates; Scalable Server Licence CHF 1,439.89 | DWG/DXF R2.5–R32 (2018–2026) | DXF/DWG (`-r` output release) | Linux | current | Paid fallback: web-service use explicitly allowed |
| Autodesk APS Model Derivative / Design Automation [25] | Cloud API | Proprietary; Model Derivative 0.1 Flex token per simple job, 0.5 per complex job; Design Automation 2.0 tokens per processing hour; tokens expire after 12 months; USD per token not verifiable (pricing page refuses automated fetch; A$4.45 on Autodesk AU, US$3 in 2022 reseller lists) | all | DXF, SVF | Autodesk cloud, US or EMEA region | current | No: plans leave the server |
| DXF-only libraries (dxf-parser, three-dxf, dxf-viewer, IxMilia dxf-rs, netDxf, kabeja) | – | MIT/Apache | **DXF only, no DWG** | – | – | – | Not converters |

Not evaluated: Autodesk RealDWG and DWG TrueView (Windows only), Aspose.CAD (commercial .NET/Python), ODA "Drawing in Web" (proprietary, marked not production-ready).

## Table 2: advantages and disadvantages

| Candidate | Advantages | Disadvantages |
|---|---|---|
| LibreDWG dwg2dxf | Reads both BBL test files completely, fast (<0.4 s per file); DXF output in any version; already wired as `LibreDwgConverter` (`dwg2dxf -y -o out src`); 15 years of reverse engineering, active in 2026; Python/Perl bindings exist | GPL-3 (fine as a separate process, not for linking); no Ubuntu package, build from source; C with a long CVE list (crafted files crash it: sandbox, timeout, memory cap); non-critical warnings on some files (test file 2 returned error code 68 = VALUEOUTOFBOUNDS 64 + UNHANDLEDCLASS 4, below the critical threshold 128 [5]) |
| libredwg-web | Same reader in the browser; both BBL files parsed in 65–174 ms, DXF written in 100–360 ms; JSON database and SVG for a quick preview | 10.8 MB wasm download per visitor; GPL-3 module shipped to the browser (Table 3); mlightcad's own Web-Worker separation is a convenience, not a licence boundary [12] |
| acadrust | MPL-2.0 (file-level copyleft, links into MIT code and into a wasm bundle); pure Rust, no C, wasm32 target; claims R12–R2018 read and write with 48 entity types; cadcraft (4.9k stars) builds on it, which will drive bug reports | Eight months old, one maintainer, no published test corpus or comparison; entity coverage per version not documented; not tested here (no Rust toolchain on this machine); needs a 30-line CLI of our own |
| cadcraft | Ready-made `cadcraft-cli convert` around acadrust; permissive | One week old; 266 open issues; DWG disabled in its own web build; drops entities it does not model on save |
| ACadSharp | MIT, mature (1.7M downloads), reads AC1014–AC1032 | Needs the .NET runtime on the VPS and a CLI of our own; no WASM build; DWG reader historically "not fully implemented" (proxy objects, meshes) |
| ezdwg | MIT, pip wheel, ezdxf-like API, DXF export | Lost LINE, LWPOLYLINE, TEXT, DIMENSION, CIRCLE of the BBL 2004 file; 6 s per file; INSUNITS written as 6 (m) where LibreDWG reads 4 (mm) |
| libdxfrw | GPL-2 CLI, FreeCAD's choice | DWG reader "experimental", versions unlisted |
| ODA File Converter | Reference-quality reader (ODA SDK), free download, ezdxf add-on exists | Non-members: "non-commercial applications only" [21]; agreement forbids "time-sharing or service bureau" use and transfer to an employer [20]; Qt GUI needs xvfb; closed binary; Arizona law |
| QCAD Pro dwg2dwg | Server licence explicitly covers "a web server to process or generate data as part of a web service" [24]; CHF 480 once; DWG up to 2026 | Proprietary binary on the server; per-server licence (scalable variant CHF 1,440); no source |
| Autodesk APS | Autodesk's own reader | Plans leave the server (US or EMEA region); per-job tokens; Autodesk account and app registration |

## Table 3: licence consequences for an MIT app on a public server

| Licence | Server, separate process (CLI, files in/out) | Server, linked library (Python binding, ctypes, in-process) | Browser WASM shipped to visitors |
|---|---|---|---|
| GPL-3.0 (LibreDWG) | Fine: the GPL program is "mere aggregation"; the app stays MIT; publish the build recipe and keep the LibreDWG source available (we build from the GNU tarball) | The combined work is a derivative: the app would have to be released under GPL-3 (BBL's outbound licence under Art. 9 EMBAG would change) | Distribution: the wasm plus its JS glue must carry the GPL notice and corresponding source; the page code that calls it is arguably part of the combined work; keep it out of the MIT bundle or do not ship it |
| GPL-2.0-only (libdxfrw-web) | Fine, as above | As above; additionally incompatible with GPL-3 code in the same program | As above, with the GPL-2/GPL-3 incompatibility |
| MPL-2.0 (acadrust) | Fine | Fine: only modified MPL files must be published under MPL; the MIT app and a static or wasm link are allowed (MPL §3.3) | Fine: ship the wasm with the MPL notice and a source link |
| MIT (ACadSharp, ezdwg, IxMilia) | Fine | Fine | Fine |
| ODA Community User Agreement (File Converter) | Not for a public service: non-member use is non-commercial only; "may not ... use any Community Application ... in any time-sharing or service bureau arrangement"; the licence is personal and "may not be transferred ... including Community User's employer"; "revocable" [20–21]. Internal federal use needs ODA's written confirmation | Not applicable (no library) | Not applicable |
| QCAD Professional Server Licence | Allowed on a web server per server; the application itself may not be offered to visitors | Not applicable | Not applicable |
| Cloud (Autodesk APS) | Conflicts with "uploads must not leave the server" and with non-public BBL plans; Autodesk terms and region selection apply | – | – |

## Test on the two public test DWGs of plan-check

`assets/test-files/` of [bbl-dres/plan-check](https://github.com/bbl-dres/plan-check) (a local clone under `docs/papers/code/`): `CAD.V01-CAFM-Plan-DE.dwg` (AC1032 = 2018, 0.4 MB) and `2011-DM-0-A04-6A1.dwg` (AC1018 = 2004, 2.0 MB). Each tool wrote a DXF that was read back with ezdxf 1.4.4, the pipeline's own reader; counts are model-space entities.

| Tool | 2018 file | 2004 file |
|---|---|---|
| LibreDWG (libredwg-web 0.8.0 wasm, Node 24) | 3,502 entities: LWPOLYLINE 3230, TEXT 86, LINE 64, HATCH 41, CIRCLE 38, MTEXT 29, ARC 12, DIMENSION 2; 17 layers, 4 blocks, 2 layouts, INSUNITS 4 (mm); read 65 ms, DXF (AC1032, 1.5 MB) 101 ms | 4,599 entities: POLYLINE 1730, LINE 994, HATCH 687, LWPOLYLINE 581, INSERT 343, TEXT 145, DIMENSION 74, CIRCLE 39, ARC 6; 32 layers, 362 blocks, INSUNITS 4; warnings (code 68, non-critical); read 174 ms, DXF (AC1018, 12.2 MB) 357 ms |
| ezdwg 0.16.0 (`to_dxf`, default R2010, dimensions exploded) | 3,530 entities (dimensions exploded into LINE/MTEXT/SOLID/POINT), INSUNITS 6 (m), 0.6 s | 1,419 entities: HATCH 719, INSERT 696, POLYLINE 4; LINE, LWPOLYLINE, TEXT, DIMENSION, CIRCLE missing; 6.2 s |
| acadrust / cadcraft, ACadSharp, ODA, QCAD | not run (no Rust toolchain, .NET runtime or licence on this machine; Docker not running) | – |

## Recommendation

1. **Server, now: LibreDWG 0.14 `dwg2dxf` as a sandboxed separate process.** Install in `app/deploy/setup.sh`: download `libredwg-0.14.tar.xz` from ftp.gnu.org (with its `.sig`), `./configure --disable-bindings --enable-release`, `make -j`, install `dwg2dxf` to `/usr/local/bin` (about 15 lines; builds in a few minutes). The existing `LibreDwgConverter` then finds it on PATH (or set `FPX_DWG2DXF`); optionally add `--as r2018` so every DXF has one version. Run it under a timeout, a memory cap and no network (`systemd-run --scope -p MemoryMax=2G` or `bwrap`), because crafted DWGs have crashed LibreDWG repeatedly; keep the 300 s timeout and treat a non-zero exit with an existing output as "converted with warnings" (codes below 128 are non-critical). Record `libredwg (GPL-3.0)` in the provenance as the code already does; mention the GPL converter and the tarball source in `app/deploy/README.md`. Effort: about one day including a test on the two repository DWGs. Licence consequence: none for the MIT app (Table 3, row 1).
2. **Next: evaluate acadrust (MPL-2.0).** Write a 30-line Rust CLI (`DwgReader::from_file` → `DxfWriter::write_to_file`, [13]), run it on the BBL corpus and compare entity counts and INSUNITS with the LibreDWG output; if it matches, add an `acadrust` `DwgConverter` and make it the default: permissive, memory-safe, no GPL on the server, and the only candidate that compiles to wasm without GPL. Do not take cadcraft as the dependency.
3. **Browser preview: not now.** The server already renders every layout through ezdxf, and the upload step shows that render within seconds; a 10.8 MB GPL wasm per DWG upload buys little. If a client-side preview becomes a requirement, either ship `@mlightcad/libredwg-web` as a separate GPL-licensed worker script with its source offered (not bundled with the MIT ES modules), or wait for an acadrust wasm build.
4. **Avoid:** the ODA File Converter on plan-extract.ch (terms above; an internal BBL instance would still need ODA's written confirmation or a membership); Autodesk APS and any cloud converter (plans leave the server); ezdwg (incomplete); libdxfrw (experimental reader); an ODA membership (US$7,500 first year for a web service). QCAD Professional's server licence (CHF 480) is an acceptable paid fallback if LibreDWG misreads a class of files, since it is explicitly licensed for web services.

**Open questions / to verify**
- LibreDWG on the full BBL corpus (20+ files across authors and years): entity counts and INSUNITS against QCAD or AutoCAD, and which files raise non-critical codes (64 = value out of bounds, 4 = unhandled class) and what they lose.
- Whether BBL's outbound licence (Art. 9 EMBAG) has any objection to a GPL tool invoked as a separate process; this note assumes not.
- acadrust fidelity (untested here) and whether its DWG reader has a failsafe mode like its DXF reader.
- The INSUNITS discrepancy (LibreDWG 4 = mm, ezdwg 6 = m) on the 2018 file: check in AutoCAD; the CAD-Richtlinie expects millimetres.
- Sandbox choice on the VPS (`bwrap` vs `systemd-run`), and memory for a 12 MB DXF from a 2 MB DWG (about 6x growth).
- QCAD's engine: Wikipedia states the DWG plugin is "based on the Open Design Alliance DWG libraries"; the qcad.org pages fetched here do not say so.
- Autodesk Flex token price in USD and the APS free tier were not verifiable (HTTP 403 on the pricing page).

## Sources

1. LibreDWG README: https://github.com/LibreDWG/libredwg
2. LibreDWG licence: https://raw.githubusercontent.com/LibreDWG/libredwg/master/COPYING
3. LibreDWG releases (0.14, 2026-08-31): https://ftp.gnu.org/gnu/libredwg/
4. dwg2dxf man page (`--as`, `-o`, `-y`): https://raw.githubusercontent.com/LibreDWG/libredwg/master/programs/dwg2dxf.1
5. LibreDWG error codes (`DWG_ERR_CRITICAL` = 128): https://raw.githubusercontent.com/LibreDWG/libredwg/master/include/dwg.h
6. LibreDWG nightly releases: https://github.com/LibreDWG/libredwg/releases
7. No Ubuntu/Debian package: https://packages.ubuntu.com/search?keywords=libredwg&searchon=names&suite=all&section=all ; https://packages.debian.org/search?keywords=libredwg&searchon=names&suite=all&section=all
8. conda-forge libredwg 0.11.3876: https://anaconda.org/conda-forge/libredwg
9. CVE history: https://stack.watch/product/gnu/libredwg/ ; openSUSE 2026 advisory https://www.tenable.com/plugins/nessus/327244
10. libredwg-web: https://github.com/mlightcad/libredwg-web ; https://github.com/mlightcad/libredwg-web/blob/master/bindings/javascript/README.md
11. libredwg-web package metadata: https://cdn.jsdelivr.net/npm/@mlightcad/libredwg-web/package.json ; https://registry.npmjs.org/@mlightcad%2Flibredwg-web ; https://data.jsdelivr.com/v1/package/npm/@mlightcad/libredwg-web@0.8.0/flat
12. libredwg-converter (Web Worker note): https://cdn.jsdelivr.net/npm/@mlightcad/libredwg-converter/README.md
13. acadrust: https://github.com/hakanaktt/acadrust ; https://raw.githubusercontent.com/hakanaktt/acadrust/main/LICENSE ; https://raw.githubusercontent.com/hakanaktt/acadrust/main/README.md ; https://docs.rs/acadrust/latest/acadrust/ ; https://crates.io/api/v1/crates/acadrust ; https://raw.githubusercontent.com/hakanaktt/acadrust/main/CHANGELOG.md
14. cadcraft: https://github.com/storytold/cadcraft ; https://github.com/storytold/cadcraft/blob/main/Cargo.toml ; https://github.com/storytold/cadcraft/blob/main/crates/dwg/Cargo.toml ; https://github.com/storytold/cadcraft/blob/main/crates/dwg/src/lib.rs ; https://raw.githubusercontent.com/storytold/cadcraft/main/NOTICE ; https://github.com/storytold/cadcraft/releases ; https://api.github.com/repos/storytold/cadcraft/releases/tags/v0.7.0
15. ezdwg: https://pypi.org/project/ezdwg/ ; https://github.com/monozukuri-ai/ezdwg ; https://monozukuri-ai.github.io/ezdwg/
16. ACadSharp: https://github.com/DomCR/ACadSharp ; https://raw.githubusercontent.com/DomCR/ACadSharp/master/LICENSE ; https://github.com/DomCR/ACadSharp/releases ; https://www.nuget.org/packages/ACadSharp
17. libdxfrw: https://github.com/LibreCAD/libdxfrw ; https://raw.githubusercontent.com/LibreCAD/libdxfrw/master/COPYING ; https://raw.githubusercontent.com/LibreCAD/libdxfrw/master/README.md ; https://cdn.jsdelivr.net/npm/@mlightcad/libdxfrw-web/README.md ; https://cdn.jsdelivr.net/npm/@mlightcad/libdxfrw-web/package.json
18. IxMilia.Dwg: https://github.com/IxMilia/Dwg
19. ODA File Converter download page (platforms, CLI): https://www.opendesign.com/guestfiles/oda_file_converter
20. ODA Community User Agreement, 5 September 2025 (PDF, text extracted): https://www.opendesign.com/files/2025-09/ODA%20Community%20User%20Agreement%2009-2025.pdf
21. ODA FAQ "What are ODA Viewer and ODA File Converter?": https://www.opendesign.com/node/3218
22. ODA membership pricing and rules: https://www.opendesign.com/pricing.md ; https://www.opendesign.com/faq/question/how-can-i-join-open-design-alliance-and-use-your-sdks.md
23. ezdxf odafc add-on: https://ezdxf.readthedocs.io/en/stable/addons/odafc.html
24. QCAD: http://qcad.org/en/command-line-tools/ ; https://qcad.org/en/license/ ; https://qcad.org/prestashop/en/qcad/30-qcad-professional-server-license.html ; https://qcad.org/prestashop/en/qcad/34-qcad-professional-server-license.html ; https://qcad.org/en/features/ ; https://en.wikipedia.org/wiki/QCAD (ODA statement, secondary)
25. Autodesk APS: https://aps.autodesk.com/pricing (figures from search snippets; fetch blocked) ; https://aps.autodesk.com/en/docs/model-derivative/v2/developers_guide/regions ; https://www-pt.autodesk.com/au/buying/flex
26. Test files: `assets/test-files/` of https://github.com/bbl-dres/plan-check ; test scripts and outputs kept outside the repository.
