# VibeWords

Collaborative crossword solving in real time.

Upload a crossword described in `.ipuz` format or try out a house puzzle!

Hosted at [vibewords.machjn.com](https://vibewords.machjn.com/).

For development, see [DEV.md](DEV.md). For outstanding items, see [TODO.md](TODO.md). 


## xw

`xw` is the vibewords CLI that is installed as part of the vibewords python package. 

It handles parsing vibeword's prototype custom `.xw` human-editable crossword format (see the [grammar definition](src/vibewords/xw.lark) and [sample](examples/french.xw) for details), exporting to `.ipuz` from `.puz` and `.xw` formats, and scraping crosswords from supported connectors.

Note that scraping is disabled in the production vibewords webapp. Scraping functionality in the CLI is intended only for private use; this project does not condone sharing crosswords scraped from sites that rely on ad revenue for their existence.


## Origins

Vibewords is, as the name suggests, a largely vibe-coded app i.e. created via natural language, without editing code directly.

It's an attempt to improve on the crossword interfaces offered by the UK newspaper sites, but is also a on-going personal experiment in exploring the capabilities of ML models.

