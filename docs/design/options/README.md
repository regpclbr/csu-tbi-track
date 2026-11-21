# Three calmer colour options (ASSUMPTIONS 413, after the UAT)

UAT comment (Community Development Project Head): "the colors are too bright." All three options keep the original
identity: the navy → blue gradient frame (original `#22378F → #60CDEE`), the floating rounded panel, frosted-glass
sign-in card, pill buttons, Inter, the navy logo tile, the filled welcome banner and "Next activity" card on the
Dashboard. They differ only in the colour tokens between `OPTION START` and `OPTION END` in
`frontend/tailwind/app.css`.

Screenshots: 1366 × 768, demo account (Demo Director), demo activity "Investor Networking"; each option's CSS was built
separately and served in place of `static/css/app.css` (that file was not changed). Every visible text was checked for
real names and the real certificate numbers: none.

| | A: Deep navy → steel blue | B: Navy → muted cyan | C: Soft navy → dusty sky |
|---|---|---|---|
| Feel | darkest, quietest | keeps the original's cyan end, toned down | lightest, closest to the original |
| Gradient | `#1A2862 → #3D6694` | `#1E2F73 → #3B8AA6` | `#25398C → #6C9DC9` |
| Hue, saturation (original 228° 62% → 194° 81%) | 228° 58% → 212° 42% | 228° 59% → 196° 48% | 228° 58% → 208° 46% |
| Light end, lightness (original 65%) | 41% | 44% | 61% |
| White on the dark end | 13.8:1 | 12.3:1 | 10.3:1 |
| White on the light end | 6.0:1 | 3.9:1 | 2.9:1 (no text sits there) |
| Primary button | `#263E86`, white on it 9.9:1 | `#2A4494`, 8.9:1 | `#2F4DA3`, 7.8:1 |
| Primary as text on the panel | 9.2:1 | 8.2:1 | 7.2:1 |
| Soft tint (current tab, chips) | `#E9ECF3`, primary text 8.4:1 | `#EAECF4`, 7.6:1 | `#EAEDF6`, 6.6:1 |
| Link colour | `#2F56B0`, 6.8:1 white / 6.3:1 panel | `#2C5AA8`, 6.7 / 6.2 | `#335BB5`, 6.4 / 5.9 |
| Light text on the gradient | `#D3DCEC`, 10.0:1 | `#D3E8F0`, 9.7:1 | `#DEE8F7`, 8.3:1 |

Shared by every option:
- **Panel and cards:** panel `#F4F6F9` (neutral light grey), white cards.
- **Text and borders:** ink `#1E293B` 14.6:1; muted `#475569` 7.6:1 on white, 7.0:1 on the panel; input border `#7B8798` 3.7:1 on white, 3.4:1 on the panel; focus ring = primary.
- **Status badges:** text on its tint with a border of the same hue.

| Status | Text | Tint | Contrast |
|---|---|---|---|
| Scheduled / Waiting for review (blue) | `#2B59A8` | `#EEF3FB` | 6.1:1 |
| Ongoing / Returned (amber) | `#8F5A0A` | `#FDF6E6` | 5.4:1 |
| Conducted / Approved (green) | `#26704A` | `#EDF7F1` | 5.5:1 |
| Overdue (red) | `#B42318` | `#FDF1F0` | 6.0:1 |
| Draft / Missing (grey) | `#475467` | `#F2F4F7` | 7.0:1 |

Files: `option-A-dashboard.png`, `option-A-activity.png`, `option-A-login.png`, and the same for B and C.
