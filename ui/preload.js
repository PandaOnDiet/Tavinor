// The only things the page can do: ask Python through the main process,
// and tell the window when to take the mouse.
const { contextBridge, ipcRenderer } = require('electron');

// In the pane's frame, mouse moves land here and never reach the notch page,
// which decides when the window takes the mouse. Without this, coming back
// onto Settings straight over its content left the window ignoring the mouse:
// clicks went through to whatever was behind (2026-10-21).
if (window.parent !== window) {
  window.addEventListener('mousemove', () => window.parent.postMessage({ tav: 'hover' }, '*'), true);
}

contextBridge.exposeInMainWorld('tav', {
  call: (tool, args) => ipcRenderer.invoke('call', tool, args),
  backdrop: () => ipcRenderer.invoke('backdrop'),
  interactive: (on) => ipcRenderer.send('interactive', !!on),
  opened: (on) => ipcRenderer.send('open', !!on),
  focus: () => ipcRenderer.send('focus'),
  // Inside the notch's pane (Settings, Chat) the page is a frame: closing it means
  // going back to Today, not closing the notch window.
  closeMe: () => (window.parent !== window
    ? window.parent.postMessage({ tav: 'close-pane' }, '*')
    : ipcRenderer.send('close-me')),
  on: (channel, fn) => {
    if (['hello', 'events', 'cursor', 'backdrop', 'dizzy'].includes(channel)) {
      ipcRenderer.on(channel, (_e, data) => fn(data));
    }
  },
});
