# VibeWords

Collaborative crossword solving in real time.

Hosted at [vibewords.machjn.com](https://vibewords.machjn.com/).

For development, see [DEV.md](DEV.md). For outstanding items, see [TODO.md](TODO.md). 

## xw

`xw` is the vibewords CLI that is installed as part of the vibewords python package. 

It handles parsing vibeword's prototype custom `.xw` human-editable crossword format (see example)[examples/french.xw], exporting to `.ipuz` from `.puz` and `.xw` formats, and scraping crosswords from supported connectors.

Note that scraping is disabled in the production vibewords webapp. Scraping functionality is intended only for private use; this project does not condone sharing crosswords scraped from sites that rely on ad revenue for their existence.
