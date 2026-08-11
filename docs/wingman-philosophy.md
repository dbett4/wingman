# Wingman visual notes

The Wingman mark is an abstract wing, drawn for a small browser-toolbar icon before
any larger use. It should read clearly at 16 pixels and avoid looking like a letter or
a generic bird silhouette.

## Shape

- The mark uses several overlapping feather shapes.
- Feather lengths increase toward the tip, creating an upward diagonal from left to
  right.
- Gaps between feathers must remain open at favicon size.
- The same geometry is used for light and dark treatments.

## Color

The palette has two colors: a graphite background and a cobalt mark. The inverted
version uses a cobalt background and a pale mark. There are no gradients, shadows, or
extra accent colors.

## Production check

Export the mark at its actual toolbar and favicon sizes, then inspect those files
without browser scaling. If the feather gaps close or the tip blurs, simplify the
geometry rather than adding detail.
