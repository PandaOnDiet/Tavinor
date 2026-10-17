// The only things the page can do: ask Python through the main process,
// and tell the window when to take the mouse.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('tav', {
  call: (tool, args) => ipcRenderer.invoke('call', tool, args),
  backdrop: () => ipcRenderer.invoke('backdrop'),
  interactive: (on) => ipcRenderer.send('interactive', !!on),
  opened: (on) => ipcRenderer.send('open', !!on),
  focus: () => ipcRenderer.send('focus'),
  on: (channel, fn) => {
    if (['hello', 'events', 'cursor'].includes(channel)) {
      ipcRenderer.on(channel, (_e, data) => fn(data));
    }
  },
});
