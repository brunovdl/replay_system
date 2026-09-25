# 🎨 MARTINS AI — Design System & Especificação Visual

Este documento define o sistema de design, guia de estilo visual, paleta de cores, tipografia e especificações de componentes da interface do **Replay Web (MARTINS AI)**.

---

## 🏛️ 1. Identidade de Marca (MARTINS AI)

A identidade visual é baseada na marca **MARTINS AI**: alta tecnologia, inteligência artificial, automação e design futurista de alta precisão (*Cyber-Premium*).

### Paleta de Cores (Color Tokens)

| Token | Código HEX / HSL | Uso Principal |
|---|---|---|
| `--color-bg-deep` | `#030712` | Fundo principal da aplicação |
| `--color-surface` | `#0b1528` | Cards, barras de navegação e painéis |
| `--color-surface-hover` | `#112240` | Estados hover/active de botões e itens |
| `--color-cyan-glow` | `#00a8e8` | **Cor primária de destaque (Neon Cyan)**, bordas ativas e luzes neurais |
| `--color-cyan-subtle` | `rgba(0, 168, 232, 0.15)` | Backgrounds de badges, seleções e sombras neon |
| `--color-metallic-silver` | `#e0e1dd` | **Prata Metálico**, títulos secundários e detalhes cromados |
| `--color-navy-dark` | `#0a192f` | Estrutura escura de botões e overlays |
| `--color-text-main` | `#ffffff` | Texto primário de alto contraste |
| `--color-text-muted` | `#94a3b8` | Textos secundários, descrições e labels |
| `--color-accent-red` | `#ff3b30` | Indicador de gravação REC e efeito de salvamento |

---

## 🔤 2. Tipografia

- **Família Tipográfica**: `Outfit`, `Inter`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `sans-serif`.
- **Pesos Utilizados**:
  - `400` (Regular) — Textos descritivos e badges secundários.
  - `600` (SemiBold) — Botões, labels e contadores.
  - `700` (Bold) — Títulos de painéis e marca MARTINS AI.
  - `800` (ExtraBold) — Botão principal REPLAY e status ativas.

---

## 🧩 3. Componentes de UI

### A. Cabeçalho & Marca (Header & Brand)
- **Logo Oficial MARTINS AI**: Exibida no topo e nas configurações utilizando as imagens oficiais da marca (`logo-horizontal.png` e `logo-official.png`), integradas nativamente ao layout escuro *Cyber-Premium*.
- **Barra de Status com Glow Cyan**: Indicador de buffer retroativo (`⏳ 12.5s / 15s` ou `🟢 15s cheio`).

### B. Viewport de Vídeo (Camera Canvas)
- **Sem Distorção**: Renderização com `object-fit: contain` e proporção dinâmica ajustada à orientação original (Paisagem 16:9 ou Retrato 9:16).
- **Overlay de Gravação Continuada**: Emblema de status `REC (ONLINE)` com anel pulsante vermelho/cyan.

### C. Botão de Disparo REPLAY
- **Formato**: Círculo de 88px com gradiente Cyan Neon (`#00a8e8` -> `#0077b6`).
- **Animação Pulsante**: Anéis de iluminação neon quando o buffer está pronto e efeito de contração hálpica ao toque.

### D. Modo Controle Remoto (Multi-Dispositivo)
- **Comportamento**: Permite que dispositivos secundários (smartphones do operador/juiz) acionem o replay retroativo sem utilizar a câmera local, mantendo a transmissão da câmera principal no tripé.

---

## 🖼️ 4. Iconografia Vetorial SVG (Sem Emojis)

Todos os emojis padrão foram substituídos por ícones vetoriais SVG customizados:
1. **`IconLogoMartins`**: Símbolo M duplo da MARTINS AI em vetor SVG.
2. **`IconCamera`**: Câmera fotográfica com lente brilhante Cyan.
3. **`IconRemote`**: Controle remoto / transmissor sem fio futurista.
4. **`IconSettings`**: Engrenagem de ajuste metálica.
5. **`IconFlipCamera`**: Alternador de câmera frontal/traseira.
6. **`IconReplay`**: Símbolo de disparo com setas de retrocesso rápido.
