# Themes

Two groups of themes, shown as two sections of the **Themes** page in
Obelus's settings:

- **Obelus** -- themes made for Obelus, one entry each in
  [`entries/`](entries/).
- **omarchy** -- every theme [omarchy](https://omarchy.org) ships,
  converted here. They need no entries (see [below](#omarchys-themes)).

## Adding a theme

1. **Make it.** Put it in `~/.config/obelus/themes/<name>/theme.toml`.
   Obelus watches that directory, so choosing the theme in Obelus shows
   every change as you save, and marks any line it cannot read. See
   [Themes](https://obelus-editor.github.io/obelus/reference.html#config) in
   Obelus's reference for what a theme file can say.

2. **Describe it.** Add an `[about]` table. Obelus's card for the theme
   shows both lines:

   ```toml
   [about]
   author = "Your Name"
   description = "One sentence about what it looks like"
   ```

   If it has a wallpaper, say whose picture it is:

   ```toml
   [wallpaper]
   file = "rain.jpg"
   license = "CC-BY-4.0"            # an SPDX identifier
   source = "https://example.com/where-it-came-from"
   ```

3. **Publish it.** Push the theme's directory to a public git repository
   of your own.

4. **List it.** Open a pull request here that adds one file,
   `themes/entries/<name>.toml`:

   ```toml
   repo = "https://github.com/you/your-themes"
   commit = "0123456789abcdef0123456789abcdef01234567"  # the whole commit
   path = "rain"                                       # the theme's directory
   ```

   The file's name is the theme's name.

To **update** the theme, push to your repository and open a pull request
that changes only `commit`. Readers who installed it then see **Update**.

## What CI checks

The pull request's checks fetch your repository at that commit and refuse
the entry if any of these fail. The summary of the check run shows the
theme's wallpaper, licence and source for review.

From Obelus itself (`obelus-check-theme`, the same reading the editor does):

- the file is TOML and every colour is one;
- `fit` and `anchor` are ones Obelus knows;
- the wallpaper is inside the theme's directory, and decodes;
- the name is not the name of a theme built into Obelus.

From this registry:

- the name is letters, digits, `-`, `_` and `.`, and does not start with
  `omarchy-`;
- `commit` is a whole commit, not a branch or a tag;
- `[about]` has an `author` and a `description`;
- a wallpaper is a jpg, png or webp, at most 3840 pixels a side and 5 MB,
  with an SPDX `license` and a `source`;
- the directory holds `theme.toml`, its wallpaper, and nothing else.

`path` may also name a single `.toml` file, for a theme with no wallpaper.

## omarchy's themes

CI renders each of omarchy's `themes/<name>/colors.toml` through Obelus's
own omarchy template (`contrib/omarchy/obelus.toml.tpl`, the one omarchy uses
on the desktop) and lists it as `omarchy-<name>`. The wallpaper is the first
picture in that theme's `backgrounds/`, made smaller if it is larger than
3840 pixels a side, credited as MIT with a link to the original in
[basecamp/omarchy](https://github.com/basecamp/omarchy). A theme's version
is the last omarchy commit that touched that theme's directory. Publishing
runs daily to pick up omarchy's changes.

## Taking a theme out

Delete its entry. The next publish takes the package off the site. Readers
who installed it keep it; Obelus marks it **No longer listed** and lets
them remove it.
