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
- issue with e.g. 
Cryptic crossword No 30,013

Major:
- Add other scrapers
- store state in db rather than memory
- add application awareness of IAP identity, so that we can have admin users who can delete rooms, infer names etc.

Ideas:
- add crossword editor?
- add solver tools like anagram helper
- scrape all publically available crosswords into ipuz format and archive?
- can we reverse engineer a grid from a list clues?

## Reverse Engineering Grids

I want to develop an algorithm that takes a list of crossword clues and their lengths, and spits out the grid. I'm not confident this is always even possible. I'd like to try however.

First, we need to boil down our list of clues into input expressing only the relevant information. The textual clues themselves be discarded, we can consider our input to be a list of tuples (word index, direction, length). The hope is that these describe a grid. But they may not.

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