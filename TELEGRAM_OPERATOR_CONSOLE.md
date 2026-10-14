# TELEGRAM OPERATOR CONSOLE

The Telegram interface is an operator console, not a demo bot.

Design rules:
- compact hierarchy instead of a wall of buttons;
- one main dashboard with system/risk/scanner state;
- market page ranks executable NET, not RAW spread;
- strategies page always shows Scan / Paper / Real separately;
- locked modes are visually explicit;
- LIVE has its own control screen;
- Emergency STOP is reachable from the main screen;
- resume wording says "check and resume", because reconciliation is mandatory;
- DEX page never implies wallet execution while it is disabled;
- exchange page emphasizes operational health and certification;
- pages use stable edited messages where possible instead of chat spam;
- financial values are formatted consistently;
- no fake profit claims and no decorative AI language.

Main navigation:
Market / Positions
Strategies / Venues
Analytics / Replay
Diary / Capital
Risk Center / System
DEX / LIVE
Scanner controls
Emergency STOP

The next UI integration pass will add:
- venue detail pages with Scan/Paper/Real toggles;
- active LIVE position card with edited PnL/NET/spread/exposure;
- capital allocation page;
- strategy detail pages;
- export/report page;
- incident/UNKNOWN order banner at highest priority.
