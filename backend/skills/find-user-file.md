---
name: find-user-file
triggers: hitta en fil, var är mitt CV, leta upp ett dokument, hitta mitt cv i nedladdningar, när uppdaterades filen senast, sök efter en pdf på datorn; find a file, where is my CV, locate a document, when was a file last updated
---
The user wants to find a file (a CV, a document, a download) somewhere on the
computer. Use `search_files(query, root?)` — it searches the user's home
directory by default and accepts a folder shortcut like `root="Downloads"`. Pass
a name substring or a glob (e.g. `query="cv"` or `query="*.pdf"`). It returns
each match's path, size and last-modified time, so you can answer "when was it
last updated". Do NOT use the project-rooted find_file for the user's personal
files, and never claim a search failed without actually calling the tool.
