# meta_tools / preview_svg

Description for the `preview_svg` MCP tool, offered to any agent with a working
directory.

Renders an SVG deterministically at three sizes on two grounds — 180px on white
and near-black, 48px, and 16px on both — and tells the caller to open the PNG
with `Read`.

It exists because routing every logo past Lens for a review was paying for a
whole agent run to buy two different things: EYES (nobody can see an SVG —
`Read` returns XML) and FRESH eyes (a drawer forgives its own work). Only the
second needs another agent. This is the first, at two turns instead of sixteen.

The real file leans hard on the 16px panel, which is the commonest fault in a
generated mark and the one a drawer will never render voluntarily.
