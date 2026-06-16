---
name: describe-project
triggers: vad är det här för typ av projekt, vad är det här projektet, vad gör det här repot, förklara kodbasen, vilken typ av projekt är det valda; what kind of project is this, what is this project, what does this repo do, explain this codebase
---
The user asks what the current/selected project is. Do NOT guess from the name.
Ground the answer in the actual files: `read_file` the README (README.md), and
if useful `package.json` / `pyproject.toml`, or `list_dir` the project root to
see its structure. Base your answer only on what those files show. The working
directory is provided in context when a project is selected.
