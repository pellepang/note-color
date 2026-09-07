"""Native window: fullscreen-capable solid-color display via pygame."""

import pygame


class Display:
    def __init__(self, size=(800, 600), fullscreen=False, fps=30):
        pygame.init()
        pygame.display.set_caption("note-color")
        self.fullscreen = fullscreen
        self.fps = fps
        self.clock = pygame.time.Clock()
        self._set_mode(size)
        self.running = True

    def _set_mode(self, size):
        flags = pygame.RESIZABLE | (pygame.FULLSCREEN if self.fullscreen else 0)
        self.screen = pygame.display.set_mode(size, flags)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self._set_mode(self.screen.get_size())

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_f:
                    self.toggle_fullscreen()
        return self.running

    def render(self, rgb):
        self.screen.fill(rgb)
        pygame.display.flip()
        return self.clock.tick(self.fps) / 1000.0

    def quit(self):
        pygame.quit()
