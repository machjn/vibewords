# To do

Minor:
- favicon /
- clean up old rooms
- show room id in rooms list /
- fix buttons changing size on toggle due to text label length x
- better organize buttons /
- logo /
- deploy as github actions rather than manually /
- add temporary auth layer? if needed
- make columns the default clue view /
- make clue area wider by default /
- address the scaling discrepancies at laptop/desktop sizes /
- pen/pencil toggle button could be icons rather than text? /
- copy link -> copy room link /
- room connection indictator should be right most, should display some text like connected/disconnected on mouseover /
- room should be a button-like element with text "Room <room id>", and the indicator inside. clicking on it creates a small submenu that lets you copy the link to the current room and also provides a link to the rooms page. /
- on landing page, URL shouldn't be guardian-specific, we should just list supported connectors /
- test proper error handling in case of malformed ipuz file etc.
- assess exposure to injection attacks?
- add some tests for scrapers etc.
- the landing page could be clearer i.e. 'create a room' section. Browse rooms should be at the top. /
- issue with composite clues in e.g. Cryptic crossword No 30,013 (fixed) /
- export should be implemented on the backend rather than the frontend, on the Puzzle object, so it can be reused by other components
- about page, link to github /
- find link to 15 squared for the puzzle? /
- show author /
- collapse controls to icons? /
- fix mobile bug where calendar widget doesn't display Or is this just a firefox responsive design mode limitation/bug? /
- bug where not all dots shown in chip. Couldn't reproduce
- could have icons pen/pencil/hand modes, then select between the 3. Hand mode lets you select without opening keyboard x
- capture colour palette, create themes /
- background image on landing page? /
- show room timer in room dropdown? /
- abstract application config like wheel delay hold, drift etc. into a yaml config file that the application reads at startup /
- room dropdown menu width should match the element width, this has some implications for its contents /
- add publish date to room menu /
- bug: when entering text before an already filled cell, that cell gets highlighted. kinda of makes sense but not the behaviour we want.
- right-click on name chip to randomize colour
- bug: clipping when scrolling clue panel /
- slightly increase author size?
- in pencil mode, do we want to skip over cells that are already filled when moving on to next cell?
- bug: the logo is clickable throught the entire left side of the top bar for some reason
- bug: clicking outside of the crossword, e.g. in empty space or on a UI element, causes the crossword to become inactive and not recieve keyboard input
- wheel should be largely translucent with letters displayed in solid ring around edge
- bug: pressing and holding on a cell often inputs on the previous cell
- bug: weird asymmetrical behaviour with two identical tabs open. one sees 2 players, the other 1
- cross out clues that are completed /
- custom name for a room /
- connector tabs on landing page? /
- add auto-generated crossword connector?
- add home-cooked connector? /
- add puzzle type? no, seems unecessary and restrictive  x
- proper adherence to the ipuz API? Though it can be vague, we should at least set type correctly.
- make single revealed and checked letters immutable too? probably for sake of consistancy
- bug: visual bug that some gridlines appear thicker than others. this seems to happen on other crossword sites too. Basically I'd have to move away from CSS and use a canvas element. Then you lose browser-native text input. Apparently some apps layer that on top of a canvas. Anyway, not doing for now x
- improve .xw format to include proper separators for parts of crossword clue. This might allow us to switch to LALR parser, and let us catch errors earlier. For example the bug where extra parentheses at the end break the look forward parsing.
- show the clue length indicators (3,3) or whatever in the clue-panel
- add support for displaying basic formatting in clues like bold/italics. How is this handled in ipuz? How should we store that info? Take clue as markdown? maybe html tags ehhhh
- support importing .xw once it stabilizes
- support export as .xw
- support author comments? is this supported in ipuz?
- content should be separate repo include as a submodule?

Major:
- Add other scrapers
- store state in db rather than memory
- add application awareness of IAP identity, so that we can have admin users who can delete rooms, infer names etc.
- support mobile? /
- support solutions, with a solutions panel that disappears at small screen sizes similar to the clue-panel
- separate layers. crossword run-time if you will, the scrapers layer on top as an application module, and finally the site logic itself
- improve terminal UI fo xw

Ideas:
- add crossword editor?
- add solver tools like anagram helper
- scrape all publically available crosswords into ipuz format and archive?
- can we reverse engineer a grid from a list clues?
- gamify - add points
- probably should in the long-term have the fifteen-squared scraper gather all the data it can (i.e. including answers and clues), then have the grid reconstructor extract from that only the data it needs to reconstruct the grid
- put the pieces together: use the reconstructed grid and the clues/answers to construct a full puzzle object from fifteensquared. then add a script which does this and exports it to ipuz
- make crossword surround be drawable? How would this work on mobile though?
- associate themes with parlance, which are basically different sets of text strings. e.g. in neon theme, 'themes' are 'vibes', reveal is 'small/big cheat?', "Clear" is "nah", check is "guess", 
- Terminal frontend for vibewords using Ink or something? Kinda overkill though

## Reverse Engineering Grids

I want to develop an algorithm that takes a list of crossword clues and their lengths, and spits out the grid. I'm not confident this is always even possible. I'd like to try however.

First, we need to boil down our list of clues into input expressing only the relevant information. The textual clues themselves can be discarded, we can consider our input to be a list of tuples (word index, direction, length). The hope is that these describe a unique grid. But they may not.

A grid is basically an NxN matrix of binary values, each representing a cell that is either black or white, (1 or 0). There is an ordering on these cells, namely left to right, top to bottom. Explicitly, for cell in position (x,y) in an n-size grid, its cell index C in the ordering is C(x,y)=ny+x

A word is defined as a contiguous run of whites (runs) contained in a column or row. A word's cell index can be defined to be the cell index of its first cell. A word's word index W is the word's position in the word cell index ranking of all words. Some corrolaries:
- for two words, A and B, W(A) > W(B), then C(A) > C(B).
- the only way two words may have the same word index is if one has direction across, the other down

We need to reverse engineer the grid from the word descriptions. We can assume all words are described by the input. A complete description of the grid would come from the tuple (word cell index, direction, length). However the information we are given is (word index, direction, length). The algorithm needs to spit out the grids that satisfy the input and the constraints. I hope that there's exactly one grid for valid input, but in general there may be 0 or more than 1.

Constraints:

Grid constraints:
- The "British grid" constraint, which I think can be formulated as that no two parallel words may be adjacent; their letters must be separated by at least one black square. I believe technically this is equivalent to the constraint that there may be no 2x2 squares of white cells.
- Grids must be half-turn rotationally symmetric. This is easy to check with matrices
- Grids must be square and of odd side length
- All white cells must be connected i.e. there may be no unconnected "islands" of white cells.
- there cannot be more than two consecutive unchecked white cells. These are known as "double unches". Technically, a checked white cell is one that has an adjacent white cell both vertically and horizontally.
- Double unches should never appear at the start or end of a word
- words are of minimum length 3 (but this information of course is contained in the input)
- at least half the letters of a word should be checked.

## playtest 1 feedback

- indicate which player you are /
- do not collapse names /
- colours aren't cycled properly? /
- checks only working clientside /
- link to the original crossword /
- bug with No. 1,891 by Filbert Independent parsing, Sunday 24th May Cryptic
- mousing over columns button causes it to lose colour?
- should be more obvious how to change name /

## mobile input feature

### prompt 
Quite a big task for you here, feel free to take your time, come up with a plan, do multiple iterations.

I want it to be easier to input letters to a vibeword crossword grid on mobile. The main current issue is that selecting a cell opens the user's keyboard; on mobile screens this typically consumes the bottom 50% of the display. I think we can do better.

I have an idea for how to approach this, but it's not fully thought out. The basic concept is that rather than opening a keyboard, the user presses and holds on a cell, and this spawns a radial UI widget with the 26 characters of the alphabet. The circle drawn by this widget is naturally divided into 26 sectors. Thus the further the user moves their finger radially from the centre point, the more precise they will be, as the width of the segment increases radially. 

The first challenge is that we cannot necessarily draw circles at the edge of the screen.

Another challenge, which applies not just to this suggestion but to any mobile-specific input system, is how to detect that we are on mobile. I suggest somehow detecting if we are using coarse or precise input, but there may be better ways.

You might decide that there are better ways to achieve the end goal.

### items

- mobile input feature: press and hold on cell, a ring appears with letters of the alphabet
- at small scales, once the clues panel has disappeared, we add a button to toggle clues panel full screen?
- show toggle for always using keyboard input in settings menu 
- backspace required 
- consecutive input. I like the idea that if you swipe along a direction, it goes into consectutive input mode, where the letter picker doesnt close after release; this would allow you to input multiple characters. maybe pressing outside the wheel then closes it. Also,the wheel can obscure the clue, so potentially show what letters you've already input somehow
- wheel should not reopen each time
- position should not change once opened
- show letters around the outside
- fix the internal ring, looks janky




## Neon grid visual feature

- hit a scaling issue where, because, obviously, now I think about it, the sizes of elements are flexible, drawing lines claims more space


Alright. I want to add some application configuration. Primarily this should be a yaml file. Then we can have a different yaml file for different environments. But any option should be able to be configured via environment variable too, I guess you can do some mapping from yaml key to env var name.

Among the configuration options should be for example, the wait duration before the wheel appears. Add anything else you thing ought to be configurable, and plumb it up. This might mean moving some values serverside that were previously hardcoded in the javascript. I'll leave it up to your discretion, we can always make modifications later.

This next thing I don't want you to do right away, but it will be the next thing I ask you to do, so keep it in mind. For each input method (we can call them connectors or whatever) of Guardian, Independent, Ipuz, I want to only enable them if they are among a list of enabled connectors in the config. So that they have to be explicitly enabled.


## Plan feedback

- Lets formalize the concept of connector. We can create a Connector base class, and we already have a Scraper base class if I'm not mistaken. The scraping connectors e.g. GuardianConnector and IndependentConnector can implement both the Scraper and Connector classes. Whereas the LocalConnector just the Connector class. Not sure if python has abstract classes but we could use those for the base classes if they exist.
- the local connector should not fail silently if an ipuz is corrupted or unreadable. We should be logging these things
- I suppose as your plan makes clear, we need to differentiate between the connectors and the creation methods, as multiple creation methods can use the same connector (calendar creation method and by URL both use the Guardian connector, for example).
- Re. locating the content, lets copy it into the dockerfile for now
- 