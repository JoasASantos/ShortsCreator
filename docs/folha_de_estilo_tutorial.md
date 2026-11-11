# Folha de Estilo: Tutorial Explicativo com Motion

Referência: @fernanndods — "Tudo que você precisa pra editar vídeo com IA"

---

## 1. Layout e Enquadramento

### Formato
- **Aspecto**: 9:16 (1080x1920 ou 1440x2560)
- **Duração**: 60–90 segundos
- **Divisão de tela**: tela dividida horizontal
  - **Metade superior (~55%)**: fundo escuro (#0A0A0A) com motion graphics
  - **Metade inferior (~45%)**: face cam do apresentador, filmado da cintura pra cima

### Face Cam
- Iluminação quente, ambiente de home office
- Enquadramento centralizado, levemente de baixo pra cima
- Sem corte seco entre takes — fluxo contínuo de fala

---

## 2. Tipografia

### Título Principal (topo do vídeo)
- **Fonte**: sans-serif bold, branca
- **Tamanho**: grande (~48–56px em 1080p)
- **Posição**: topo da metade superior, centralizado
- **Exemplo**: "Tudo que você precisa pra editar vídeo com IA"
- Permanece fixo durante todo o vídeo (ou durante introdução)

### Legendas de Narração (karaoke-style)
- **Fonte**: sans-serif bold, branca
- **Tamanho**: médio-grande (~36–44px)
- **Posição**: centro vertical entre as duas metades (zona de transição)
- **Comportamento**: sincronizada com fala — 2 a 4 palavras por vez
- **Sem fundo/caixa** — texto direto sobre a imagem
- **Destaque**: palavra-chave pode ser colorida (amarelo ou laranja)

### Títulos de Passo
- **Formato**: "*Passo N.* **Ação**" (itálico + bold)
- **Exemplo**: "*Passo 1.* **Monta o ambiente**"
- **Posição**: topo da área de motion
- **Tamanho**: ~40px

---

## 3. Paleta de Cores

| Elemento | Cor | Hex |
|----------|-----|-----|
| Fundo motion | Preto quase puro | #0A0A0A |
| Texto principal | Branco | #FFFFFF |
| Número do passo | Cinza claro / itálico | #CCCCCC |
| Ação do passo | Branco bold | #FFFFFF |
| Destaque legenda | Amarelo quente | #F5A623 |
| Ícones macOS | Azul Finder padrão | #4A9EF5 |
| Links / CTAs | Laranja | #E8744F |

---

## 4. Motion Graphics e Animações

### Ícones e Elementos Visuais
- **Estilo**: ícones de apps reais (macOS style — Finder folders, app icons)
- **Tamanho**: ~100-120px
- **Entrada**: fade in + scale up suave (0.3–0.5s ease-out)
- **Organização**: alinhados em grid ou árvore hierárquica

### Diagramas
- **Árvore de pastas**: pasta-mãe no topo, linhas conectoras cinzas, subpastas abaixo
- **Conexões**: linhas pontilhadas animadas (ponto a ponto, da esquerda pra direita)
- **Cards de referência**: mockups de Reels/posts do Instagram com bordas arredondadas

### Transições entre Passos
- **Cross-fade suave** entre blocos de conteúdo na metade superior
- **A face cam permanece contínua** (sem corte)
- Elementos antigos saem com fade out, novos entram com fade in + slide sutil

### Mockups de Interface
- Screenshots de Claude Code desktop (janela com botões macOS vermelho/amarelo/verde)
- Texto do prompt legível dentro da janela
- Estilização clean, sem sombras pesadas

---

## 5. Ritmo e Estrutura Narrativa

### Estrutura
1. **Gancho** (0:00–0:05): problema + promessa ("ninguém conta como")
2. **Apresentação de passos** (0:05–1:05): 4 passos numerados
3. **CTA** (1:05–1:15): "Comenta MOTION que eu te mando"

### Ritmo de Corte
- Sem jump cuts na face cam
- Motion muda a cada ~5-10 segundos (acompanhando passos da narração)
- Legendas mudam a cada 0.5–1.5 segundos (2–4 palavras por bloco)

### Cadência de Fala
- Rápida e assertiva (~180 palavras/minuto)
- Sem pausas longas
- Tom: tutorial direto, sem enrolação

---

## 6. Áudio

### Narração
- Voz natural, sem efeito
- Microfone de lapela ou shotgun (sem eco)
- Volume consistente

### Música
- Sem música de fundo (ou muito sutil)
- Áudio original puro

### Efeitos Sonoros
- Nenhum visível neste exemplo
- Opcional: whoosh suave nas transições de ícones

---

## 7. Elementos Visuais Recorrentes

### Ícones que Aparecem
- App icons (Claude Code, Chrome, Safari, FFmpeg, etc.)
- Pasta Finder macOS (azul, com thumbnails)
- Mockups de Reels do Instagram (3 cards lado a lado)
- Janela de chat do Claude (mockup da interface desktop)

### Marca do Criador
- Handle "@fernanndods" com ícone do Claude no canto inferior
- Barra de comentário do Instagram no final (CTA)
- Estilo "Comenta [PALAVRA]" como call-to-action

---

## 8. Storyboard (última cena)

O vídeo mostra um storyboard renderizado pelo Claude com:
- Cards escuros com thumb do vídeo + descrição textual
- Cada card: número do passo, tipo de cena, timecode, transcrição, notas visuais
- Layout vertical (feed-like)

---

## 9. Regras para Reprodução Programática

### Com HyperFrames
```
Composição HTML:
- Background: div preta fixa (55% top)
- Face cam: vídeo posicionado na metade inferior
- Overlay de texto: posicionamento absoluto, z-index alto
- Ícones: imagens com CSS animation (keyframes fade+scale)
- Legendas: div centralizada com troca dinâmica de texto
```

### Sequência de Produção
1. Gravar face cam (vídeo bruto)
2. Transcrever com Whisper (gerar timecodes)
3. Gerar legendas sincronizadas
4. Montar motion graphics para cada passo (ícones, diagramas)
5. Compor tudo em HyperFrames (HTML + CSS + timing)
6. Renderizar MP4 final com FFmpeg

### Parâmetros Chave
- `fps`: 30
- `resolução`: 1080x1920
- `codec`: H.264
- `bitrate`: 8-12 Mbps
- `formato_legenda`: word-by-word sync, 2-4 palavras por bloco
- `motion_easing`: ease-out para entradas, ease-in para saídas
- `duração_transição`: 300-500ms
