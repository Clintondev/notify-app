// content.js
console.log("Notification Catcher script injetado!");

// Monitorar o título da página para mudanças
let lastTitle = document.title;

const observer = new MutationObserver(mutations => {
  // Lógica antiga para monitorar o título (ainda útil)
  if (document.title !== lastTitle) {
    lastTitle = document.title;
    console.log(`Mudança de título detectada: ${lastTitle}`);
    
    fetch("http://localhost:3000/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app: "Browser", text: `Título alterado para: ${lastTitle}` })
    }).catch(err => console.error("Erro ao contatar o servidor local:", err));
  }
  
  // --- LÓGICA ATUALIZADA PARA CAPTURAR NOTIFICAÇÕES INTERNAS ---
  mutations.forEach(mutation => {
    mutation.addedNodes.forEach(node => {
      // Garante que o 'nó' é um elemento HTML (nodeType 1)
      if (node.nodeType === 1) { 
        
        // Estratégia 1: Procurar pelo data-testid (do site anterior)
        const closeButton = node.querySelector('[data-testid="notification-close-button"]');
        
        // Estratégia 2: Procurar pelo texto "acenou para você"
        const hasWaveText = node.innerText && node.innerText.includes('acenou para você');

        if (closeButton || hasWaveText) {
          
          let appName = "WebApp";
          if(closeButton) {
            appName = "Site 1"; // Mude para o nome do primeiro site
            console.log("NOTIFICAÇÃO DETECTADA PELO data-testid!");
          }
          if(hasWaveText) {
            appName = "Gather"; // Ou o nome do segundo site
            console.log("NOTIFICAÇÃO 'ACENOU' DETECTADA PELO TEXTO!");
          }
          
          const notificationText = node.innerText.split('\n')[0];

          fetch("http://localhost:3000/notify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
              app: appName,
              text: notificationText 
            })
          }).catch(err => console.error("Erro ao contatar o servidor local:", err));
        }
      }
    });
  });
});

// Inicia a observação no corpo do documento
observer.observe(document.body, { childList: true, subtree: true });
