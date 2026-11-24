# syn

A command line toolbox that reads and changes Salesforce metadata with far less text than `cat`, `grep`
and hand-edited XML. No AI inside: plain code, for a person or for a coding agent of any harness.

## Install on Windows

No Python needed. Open PowerShell and paste this line:

```powershell
irm https://raw.githubusercontent.com/Dyarchia/dyarchia-syn/main/install.ps1 | iex
```

Close the terminal, open a new one, and check with `syn --version`. To update, paste the line again.
To uninstall, delete `%LOCALAPPDATA%\Programs\syn`.

## Use it

Run it from the root of a Salesforce DX project. `syn COMMAND --help` gives the exact syntax.

```text
Command                       What it prints or does
----------------------------  ----------------------------------------------------------------
syn map                       One-screen overview of the repository
syn find NAME                 Where a name is defined and who mentions it
syn rows FILE [BLOCK]         The repeated blocks of a metadata XML file as a table
syn set FILE BLOCK KEY F=V    Change fields of one row; only the bytes of that row change
syn add FILE BLOCK F=V        Add a row
syn del FILE BLOCK KEY        Remove a row
syn flow FILE                 A Flow as an outline in execution order
syn diff                      What changed in the meaning of metadata files, without XML noise
syn tests FILE                Condense the JSON of `sf apex run test --json`
syn errors FILE               Condense the JSON of `sf project deploy start --json`
syn log FILE                  Condense an Apex debug log
syn index                     Rebuild the name index from scratch
syn org                       One line per authenticated org
```

- `FILE` may be a quoted glob: one command reads or changes many profiles or permission sets.
- `find` and `map` keep an index in `.syn/` at the repository root; add it to `.gitignore`.
- No command deploys or changes anything in an org.

To let a coding agent use it, tell it in its instructions file that `syn` exists and to prefer it over
reading XML.

## From source

Python 3.12 or newer, no dependencies. `py -m pip install -e .` and `py -m pytest -q`.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
