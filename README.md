# The Obelus registry

What [Obelus](https://github.com/obelus-editor/obelus) can fetch for you,
listed in one place. Each kind of thing has a directory of its own:

- [`themes/`](themes/) -- colour themes, some with a wallpaper. Obelus lists
  them on the **Themes** page of its settings.

More kinds will come the same way (plugins, key tables), each a directory
beside `themes/`.

## How it works

This repository holds *entries*, not the things themselves. An entry names
a repository, a commit and a path; what is there stays in its author's
repository. When an entry is merged, CI fetches it, checks it, packs it and
publishes it with an index on GitHub Pages:

    https://obelus-editor.github.io/registry/themes/index.json

That site is all Obelus reads. A package is a copy, so a theme stays
installable even if its author's repository goes away; it just stops
getting updates. Taking an entry out of this repository takes it off the
site too.

The site is the `gh-pages` branch. It is rebuilt from scratch on every
publish as one commit, so it holds only what is listed now.

## Adding something

See the README in the directory for that kind -- [themes](themes/README.md).

## Licence

The scripts and workflows here are under the Apache License 2.0, like Obelus.
Everything listed is under its own author's licence; each theme's wallpaper
names its licence and where it came from.
