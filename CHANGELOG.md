# Changelog

## [1.2.10] - 2026-11-04

### Changed

- `FlowBench-Uninstall.exe` now opens the same Fluent window as the setup wizard instead of plain message boxes: confirm (with the install location), a progress bar that names each file being removed, and a done page. It follows FlowBench's own language, light/dark theme and accent colour, falling back to the Windows language.
- The uninstaller can also delete `%APPDATA%\FlowBench` (settings, authorized targets, plugins, logs) when **Also delete my settings, plugins and logs** is ticked; it is unticked by default and the box warns that this cannot be undone.
- A running FlowBench or a locked install folder now gets the same "quit FlowBench, then Retry" prompt as setup. `/S` and the other quiet switches still uninstall without any window.
- Reinstalling with the setup wizard uses the language, theme and accent from an existing FlowBench install instead of the system defaults.
- The uninstaller now bundles Qt, so it is larger than the old standard-library one; unused Qt parts (software OpenGL, PDF, QML, networking, Qt translations) are left out to keep it small.

### Verified

- Checked the uninstall window's confirm, progress and done pages in Chinese light, Chinese dark and English light, and ran the real uninstall entry point end to end (English, dark theme and a custom accent taken from FlowBench's settings).
- Covered the shared uninstall session, keeping or deleting user data, a running FlowBench, a locked folder with Retry, silent mode and preference parsing with automated tests.
- Passed the full automated test suite (133 tests, 1659 subtests).

## [1.2.9] - 2026-11-04

### Changed

- Charts on Home and Monitor zoom smoothly: each wheel notch animates the time axis to 80 % (or 125 %) around the point under the cursor, and the value axis fits the visible part. Once zoomed, drag left or right to pan, including back to where you were; double-click or zoom all the way out to resume live scrolling. The left/right-drag box zoom, which could not be dragged back, is removed. At the full view, scrolling down over a chart scrolls the page.
- Ctrl+1…Ctrl+6 now follow the sidebar order: Ctrl+4 opens Server Agents (previously unbound), Ctrl+5 Monitor and Ctrl+6 Plugins.
- Server Agents: the control URL and token each take a full row under one label column, so a URL is readable at the minimum window width.
- The trend charts on Home and Monitor use the full card height; the Home chart no longer drops axis labels at 1360×860.
- The plugin search box's suggestions button is a drop-down chevron (pointing up while open) instead of the arrow that the sort button next to it also uses.
- "Last error: —" is shown in gray while there is no error, and in red only for a real failure.
- Report target labels read `http://127.0.0.1` instead of `HTTP://127.0.0.1`, and the summed rate cap reads `150 QPS` instead of `150.0 QPS`.

### Fixed

- Switching pages with page animations on leaked several hundred animation objects per switch; they are now freed, and the Qt "animation without target" warnings are gone.
- When a stress test ends, the live card now shows the final totals from the report and resets Live QPS and Active Threads to 0; it used to keep the last half-second snapshot (for example 306 sent while the report said 322, and 12 active threads after the end).
- The report duration no longer includes the time spent waiting for in-flight requests after the test window: a 1-second TCP test against a silent server reported 2.9 seconds.
- On the Stress Test page the Protocol and duration-unit boxes have the same height as the number boxes beside them, so their labels line up.
- The number boxes under Settings → Defaults have one width and line up on the right.
- Clicking **Check for Updates** again within 10 seconds no longer re-enables the button while the second check is still running.
- `add_authorized()` returns `True` for a target that is already authorized and `False` for an empty one, instead of `None`, which callers reported as a save failure.

### Verified

- Ran a live three-target HTTP stress test against a local server (success, refused, HTTP 500) and checked that the live card, per-target line and report agree.
- Checked every page in Chinese and English, light and dark, at 1360×860 and at the 1080×700 minimum size.
- Passed the full automated test suite (124 tests, 1659 subtests).

## [1.2.8] - 2026-11-03

### Added

- Replaced Inno Setup with FlowBench's own installer (`flowbench_installer/`): a bilingual Fluent-style wizard that follows the light/dark theme, installs per user to `%LOCALAPPDATA%\Programs\FlowBench` without administrator rights, swaps the new files in only after they are fully extracted (a failed or cancelled install keeps the previous version), registers Start menu/desktop shortcuts with the FlowBench AppUserModelID and an entry in Settings → Apps, and removes an older Inno Setup install (1.2.7 and earlier) automatically.
- Added in-app updates: **Update now** downloads the release installer with a progress bar (size and, when GitHub provides it, SHA-256 verified; Retry on failure), closes FlowBench, installs in place, and reopens FlowBench. A failed update reopens the existing version. Updating from 1.2.7 or earlier still needs one manual install of 1.2.8.
- Added `FlowBench-Uninstall.exe`, which keeps settings and logs in `%APPDATA%\FlowBench`.
- Updated the Japanese language pack to 1.0.3 with translations for the update download dialog.

### Changed

- `build_installer.ps1` now needs only Python and PyInstaller; `installer.iss` is removed. Upload `FlowBench-Setup-<version>.exe` to the GitHub release under that exact name for the in-app updater.

### Verified

- Built the uninstaller with PyInstaller and ran its two-stage uninstall end to end; covered install, upgrade, rollback, cancel, unsafe paths, legacy migration, update mode, and download verification with automated tests.
- Checked the setup wizard and update window in Chinese and English, light and dark.
- Passed the full automated test suite (109 tests, 1652 subtests).

## [1.2.7] - 2026-11-02

### Added

- Added search to the Server Agents multi-select panel: filter by name, ID, hostname, platform, or online status with space-separated terms; Select All and Clear act on the visible results only.
- Restyled the server checklist with Windows/Linux icons, hostname details, and online/offline status pills in place of the plain gray list.
- Added `build_installer.ps1` to build the installer in one step, checking PyInstaller, Inno Setup, and version consistency before the build starts.
- Updated the Japanese language pack to 1.0.2 with translations for the search and server-row text.

### Fixed

- Releasing a mouse drag over a server row no longer toggles that row; a click must start and end on the same row.
- Duplicate agent IDs now appear once instead of leaving an extra row that search and Select All ignored.
- Malformed agent metadata no longer prevents the selection panel from opening.
- Rows hidden by a search no longer keep their hover highlight when shown again.
- `build_all.ps1` and `build_agent.ps1` now stop at the first failed PyInstaller run instead of continuing with stale or missing outputs.

### Verified

- Verified scrolling and mouse-wheel input with 60 servers and responsive search with 300.
- Passed the full automated test suite (75 tests, 1616 subtests).

## [1.2.6] - 2026-10-24

### Added

- Added multi-server selection for Server Agents with a scrollable checklist, Select All, Clear, and selected-count feedback.
- Added batch start and stop dispatch with per-node failure isolation and aggregated operation results.
- Updated the existing Japanese language pack to 1.0.1 with translations for the multi-server controls.

### Verified

- Verified the Server Agents selection panel with mouse-wheel scrolling and checkbox state retention.
- Verified Chinese and English UI text plus light and dark theme rendering.
- Passed the full automated test suite before packaging.

## [1.2.5] - 2026-09-30

### Added

- Added a live, bilingual Test Plan card below the local runtime status, showing target count, duration, total concurrency, total rate cap, protocol, and authorization progress without starting a test.
- Added regression coverage for all seven built-in pages, primary dialogs, placeholders, tooltips, and startup branding across Chinese/English and light/dark combinations.

### Changed

- Replaced the executable, shortcut, taskbar, and tray branding with the supplied ICO and selected the matching light/dark PNG for the startup splash.
- Unified the application, updater, installer configuration, and current documentation on version 1.2.5.

### Fixed

- Prevented the runtime status from stretching when HTTP/HTTPS-only controls are displayed, and kept the new plan card top-aligned in wide and stacked layouts.
- Preserved the plan card's typography and protocol badge styling after theme switches.
- Made the splash subtitle follow the selected interface language.
- Made the plugin publishing selector use the current language for bilingual plugin names, including disabled plugins, while preserving both translations in the generated JSON.

### Verified

- Passed all 45 automated tests and 147 subtests, including protocol-switch layout and queued-start cancellation regressions.
- Visually checked all seven built-in pages and the splash using the Windows Qt backend in Chinese/English and light/dark modes.
- Built the Windows GUI, Agent, Hub, and operator CLI, and verified packaged GUI startup in all four language/theme combinations.
- Verified the packaged version, Test Plan implementation, nine embedded icon sizes, theme-specific splash assets, and bundled server templates.
- Built the bilingual Windows installer with Inno Setup 6.7.3.

## [1.2.4] - 2026-09-20

### Added

- Added a public MQTT relay mode for Server Agents. Relay node bundles no longer require users to enter a public server IP or domain, and servers only need outbound internet access instead of an open inbound TCP 8787 control port.
- Added automatic relay URL and controller-token generation and persistence so newly generated nodes can be controlled immediately and additional nodes can join the same private relay channel.

### Changed

- Unified the desktop app, updater, installer, documentation, and release metadata on version 1.2.4.
- Made installer migration prompts follow the selected setup language instead of mixing Chinese and English.
- Refined the application layout with more consistent page margins, card spacing, top alignment, navigation reflow, and responsive two-column-to-stacked breakpoints across the primary views.
- Improved the Server Agents and Collaborative Testing pages so controls remain usable and visually balanced at both wide and narrow window sizes.

### Fixed

- Preserved the configured accent color when switching between light and dark themes.
- Cancelled locally queued stress tests when Stop is pressed during the short startup-render delay.
- Cancelled queued collaborative remote starts when a stop command arrives, including starts waiting for a previous job to finish.
- Restored the previous report, or the empty report state, when a test fails or is cancelled before startup.
- Prevented importing an already-installed folder plugin from deleting its own source directory.
- Recognized plugin files with uppercase or mixed-case `.py` extensions.

### Verified

- Added regression coverage for theme accent persistence, queued-start cancellation, version consistency, and English UI text.
- Passed all 29 automated tests, packaged-application startup smoke testing, and the Inno Setup 7 installer build.

## [1.2.2] - 2026-09-07

### Added

- Added a cross-platform headless server-agent mode with an authenticated control Hub, outbound-only polling, heartbeat reporting, and persistent command cursors.
- Added a “Server Agents” page to the desktop GUI for refreshing agents, starting and stopping authorized jobs, and viewing live node status and operation logs.
- Added one-click server-node bundle generation for Windows and Linux, including Hub and Agent credentials, generated HTTPS certificates, startup scripts, and bounded configuration.
- Added server-node status tiles for total requests, successes, live QPS, and total traffic, with per-node state and traffic details.
- Added agent job progress reporting and completion summaries, including total packets, successes, failures, latency, and sent bytes.
- Added Docker deployment files and Railway guidance for Hub/Agent bundles.
- Added English release notes and updated bilingual documentation in `SERVER_AGENT.md`.

### Changed

- Kept the Stress Test page focused on local jobs; server jobs now display their live status on the Server Agents page.
- Preserved the Collaborative Testing node-status card and added total sent traffic.
- Improved dark-mode consistency for dialogs, status editors, theme-color swatches, and hover backgrounds.
- Unified application, installer, updater, and release metadata to version 1.2.2.

### Fixed

- Fixed a packaged-application startup failure where the first PySide6.QtCore import could fail with a missing DLL entry point; bundled PySide6/shiboken6 DLL directories are now registered and Shiboken/QtCore are preloaded in frozen builds.
- Excluded incompatible bundled ICU DLLs so Qt uses the Windows-provided ICU runtime instead of a conflicting ICU 78 runtime.
- Removed automatic marketplace PR merging; external submissions now remain open for manual maintainer review.
- Kept the legacy marketplace workflow filename as a no-op guard so older clients cannot re-enable automatic merging.
- Fixed server-node completion logs repeating after polling resumed; completion entries are now deduplicated per node and job.
- Fixed startup races in all-in-one Linux bundles by waiting for the Hub `/health` endpoint before starting the Agent.
- Fixed self-signed control-plane TLS handling with a GUI compatibility retry when the generated CA file is unavailable; controller tokens are still required.
- Fixed Hub handling of aborted TLS handshakes and HTTP clients pointing at HTTPS ports, preventing noisy disconnect errors.
- Fixed Hub and Agent binding/URL mismatch in generated bundles when a non-default public port was specified.
- Fixed dark-mode dialog fallback and a potential settings-card color import issue.
- Prevented page controls from remaining transparent after rapid page switches or missed animation callbacks.

### Verified

- Agent/Hub unit tests passed: 6/6.
- Python compilation and AST checks passed.
- Light/dark UI smoke tests passed across all primary pages and dialogs.
- Windows Agent, Hub, CLI, desktop GUI, and Inno Setup installer builds completed.

## [1.2.0] - 2026-08-24

### Added

- Added smooth staggered control reveals to startup and every main page, including the plugin marketplace.
- Added a bilingual Page Animations setting that applies immediately and is included in preference backup, restore, and reset workflows.

### Changed

- Registered the system tray icon at application startup so it remains available throughout the running session.
- Disabled both page transitions and control reveals when Page Animations is turned off.

### Fixed

- Prevented page text from appearing for one frame before its entrance animation begins.
- Cleaned up active effects during rapid page switches and when animations are disabled at runtime.
- Added transition-stop compatibility for current and newer QFluentWidgets stacked-widget implementations.
- Included first-launch dashboard animation and first-visit marketplace animation.
- Corrected stale marketplace checksums for the Traditional Chinese, Lucky Wheel, and Quick Notes plugins.

### Verified

- Confirmed that disabling automatic update checks takes effect immediately without restarting the application.
- Passed English-mode runtime scans across all main pages and both marketplace tabs with no visible Chinese text.
- Passed dark-mode rendering checks across all main pages.
- Passed animation cleanup, rapid-switching, settings persistence, and tray startup checks.

## [1.1.9] - 2026-08-24

### Changed

- Removed the unused "Copy Public Address" button from collaborative host controls.
- Kept invite-code and LAN-address copy actions unchanged.

## [1.1.8] - 2026-08-24

### Fixed

- Hardened concurrent settings writes to prevent background tasks from overwriting each other's changes.
- Improved collaborative host and port validation, including IPv6 support.
- Fixed UDP testing for IPv6 targets and corrected HTTP request-body traffic accounting.
- Completed English labels for the built-in CVE plugin.
- Improved tray behavior with stable Windows app identity and safer delayed registration.
- Kept dynamically loaded plugin pages in the scrollable navigation area.

## [1.1.7] - 2026-08-23

### Fixed

- Fixed marketplace index requests falling back to the offline cache when an expired GitHub token was present.
- Kept the cached GitHub login available for author-only marketplace actions after token cleanup.
- Synced the Settings page immediately after disabling automatic update checks from the update prompt.

### Verified

- English-mode UI smoke check passed with no visible Chinese text in the loaded application widgets.
- Dark-mode theme switching passed in the Qt offscreen regression check.
- Marketplace index fetch returned live entries instead of the local cache.

## [1.1.6] - 2026-08-22

### Fixed

- Fixed marketplace refreshes returning stale indexes immediately after a plugin publish.
- Fixed rapid marketplace refresh clicks duplicating plugin cards in the UI.
- Preserved registered plugin protocols when importing stress-test configurations.
- Clamped imported ports, thread counts, rate limits, and durations to supported ranges.
- Rejected malformed bracketed IPv6 host/port values in collaborative direct-connect mode.
- Reset relay mode state cleanly when a collaborative host session is shut down.
- Completed English labels in the built-in Test Environment plugin, including localized time-zone display.
- Updated the marketplace checksum for the corrected Test Environment plugin.

### Improved

- Refined tray-menu positioning so the complete menu remains visible near screen edges.
- Made plugin lists independently scrollable while keeping page controls visible.
- Improved plugin-market and report-menu animation compatibility with current QFluentWidgets versions.
- Updated installer metadata, documentation, and the displayed application version to `1.1.6`.

### Verification

- Python bytecode compilation passed for the application and marketplace plugins.
- Qt offscreen smoke startup passed for the main window and all primary views.
- English-mode UI smoke check found no visible Chinese text in the loaded application widgets.
