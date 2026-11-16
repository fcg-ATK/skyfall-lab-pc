# Chinese UI fonts

The two font files are unmodified Noto Sans SC OpenType fonts:

| Project file | Family / style |
| --- | --- |
| NotoSansSC-Regular.otf | Noto Sans SC / Regular |
| NotoSansSC-Bold.otf | Noto Sans SC / Bold |

Copyright notice from the files' OpenType `name` tables:

© 2014-2020 Adobe (http://www.adobe.com/).

Both font files identify their license as the SIL Open Font License, Version 1.1.
The complete official license is included in `OFL.txt`. It was read from the
Noto CJK repository's official raw license on 2026-11-15; the text is preserved
with normalized paragraph spacing because the direct shell download was unavailable.

- Official project: https://github.com/notofonts/noto-cjk
- Official license: https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE
- License text reference: https://openfontlicense.org/open-font-license-official-text/

The files were copied without conversion, subsetting or glyph modification.
Only their filenames were simplified. Runtime font assets embed the original
font bytes using Unreal's FontFace INLINE loading policy. Preserve these notices
and `OFL.txt` when distributing the bundled font resources.

`tools/ui_fonts.py` imports these source files. It records their SHA-256 hashes
and the generated FontFace/UFont paths in `Saved/ui-fonts.json` when executed.
