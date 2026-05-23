# To do

Minor:
- favicon
- clean up old rooms
- show room id in rooms list
- fix buttons changing size on toggle due to text label length
- better organize buttons
- logo
- deploy as github actions rather than manually
- add temporary auth layer? if needed
- make columns the default clue view
- make clue area wider by default
- address the scaling discrepancies at laptop/desktop sizes
- pen/pencil toggle button could be icons rather than text?
- copy link -> copy room link
- room connection indictator should be right most, should display some text like connected/disconnected on mouseover
- room should be a button-like element with text "Room <room id>", and the indicator inside. clicking on it creates a small submenu that lets you copy the link to the current room and also provides a link to the rooms page.
- on landing page, URL shouldn't be guardian-specific, we should just list supported connectors
- test proper error handling in case of malformed ipuz file etc.
- assess exposure to injection attacks?
- add some tests for scrapers etc.
- the landing page could be clearer i.e. 'create a room' section. Browse rooms should be at the top.

Major:
- Add other scrapers
- store state in db rather than memory
- add application awareness of IAP identity, so that we can have admin users who can delete rooms, infer names etc.

Ideas:
- add crossword editor?
- add solver tools like anagram helper
- scrape all publically available crosswords into ipuz format and archive?