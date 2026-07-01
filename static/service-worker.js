self.addEventListener('install', function(event){
  self.skipWaiting();
});

self.addEventListener('activate', function(event){
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function(){
  // Nessuna cache applicativa: manteniamo i flussi live dell'app.
});
