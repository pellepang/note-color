# All three terminal views clear the screen on a detected size change, not just once at startup

They normally repaint via cursor-addressing (not a full clear) every frame
to avoid flicker, which only overwrites the region the current frame
actually draws. Under a tiling WM the terminal window resizes constantly as
tiles rearrange; without a resize-triggered clear, content from the previous
(larger) size/layout was never overwritten and lingered as ghost/duplicated
elements. Each display class tracks `self._last_size` and clears once when
`shutil.get_terminal_size()` differs from the last frame's.
