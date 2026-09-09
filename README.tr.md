# Taprivo

**Kodunu hareketle güçlendir.** ⚡

[English README](README.md)

Taprivo, el hareketini AI kodlama ajanları için oyunlaştırılmış bir iş
bütçesine dönüştürür. Kameranın önünde ellerinizi sıkın (ya da simülatörde
tuşlara basın), Motion Energy biriktirin ve Claude Code bu bakiyeyi MCP
üzerinden okuyup harcasın. El takibi cihazınızda yerel olarak çalışır; kamera
kareleri cihazınızdan çıkmaz. Motion Energy bir oyun mekaniğidir; API token'ı
veya kredi değildir.

> **Durum: beta sürecinde (0.1.0b1).** Kamerayla sıkma algılama ve kalibrasyon,
> klavye simülatörünün yanında kullanılabilir. Apple Silicon macOS üzerinde
> dahili FaceTime kamerayla test edilmiştir.

## Ne yapar?

- Her geçerli vuruş oturum bakiyesine 10 Motion Energy ekler (üst sınır 10.000); bir kamera sıkması beş vuruş sayılır (50 enerji).
- Küçük, her zaman üstte duran HUD (koyu veya açık tema) enerjiyi, parmak sayımlarını, combo'yu, hızı, kamera ve MCP durumunu gösterir.
- `http://127.0.0.1:32145/mcp` adresindeki yerel MCP sunucusu `get_energy`,
  `spend_energy`, `get_stats` ve `get_session` araçlarını sunar.
- Claude Code, seçtiğiniz talimat dosyasına uyarak büyük bir uygulama
  hamlesinden önce bakiyeyi okur ve uygun miktarı harcar.
- Makinenizdeki tüm Claude Code projeleri tek bakiyeyi paylaşır.

## Motion Energy nasıl işler?

```
available = generated - overflow - spent
```

`generated` tüm vuruşları sayar, `overflow` bakiye doluyken kaybolan
enerjidir, `spent` ajanların harcadığı miktardır. Harcama atomiktir: iki
istemci aynı enerjiyi harcayamaz; aynı request id ile tekrarlanan harcama iki
kez tahsil etmez, önceki sonucu döndürür.

Enerji token, kredi ya da izin **değildir**. Mevcut görev ve ajan izinleriniz
aynen geçerlidir; enerjinin bitmesi sizi engellemez, bütçe istendiği an
kapatılabilir.

Taprivo kapanınca bakiye sıfırlanır. Kalıcı geçmiş ileriki bir sürümde gelecek.

## Gereksinimler

- Apple Silicon macOS (diğer platformlar test edilmedi)
- Python 3.12 veya 3.13 ve [uv](https://docs.astral.sh/uv/)
- Ajan entegrasyonu için [Claude Code](https://code.claude.com)

## Hızlı başlangıç (simülatör, kamerasız)

```bash
git clone https://github.com/yorulmazsinan/taprivo.git
cd taprivo
uv sync
uv run taprivo simulate
```

HUD simülatör açık olarak gelir. `1`–`5` tuşları başparmak, işaret, orta,
yüzük ve serçe parmağı vurur. Her basış 10 enerji ekler.

Kamerayla: HUD'daki **Open Camera** düğmesine tıklayın veya `uv run taprivo calibrate` çalıştırın — bkz. [Kamera](#kamera).

## Claude Code bağlantısı

```bash
uv run taprivo setup claude --install-instructions
uv run taprivo doctor
```

`setup claude`, resmi `claude mcp add` komutuyla kullanıcı kapsamında `taprivo`
adlı HTTP MCP sunucusunu kaydeder, `~/.claude/taprivo.md` dosyasını yazar ve
`--install-instructions` ile bu dosyayı `~/.claude/CLAUDE.md` içinde işaretli
bir blokla içe aktarır (önce yedek alınır). `--dry-run` hiçbir şey yazmadan
değişiklikleri gösterir. `doctor` uç noktayı, token'ı, kaydı ve talimat
dosyalarını denetler.

Yapılandırmayı bir depo içinde paylaşmak için `uv run taprivo setup claude
--project` çalıştırın. Bu, token'ı `TAPRIVO_TOKEN` ortam değişkeninden alan bir
`.mcp.json` girdisi yazar; gizli değer commit edilmez.

`uv run taprivo remove claude` kaydı geri alır ve yalnızca Taprivo'nun eklediği
dosya ve blokları kaldırır.

## Kamera

Taprivo kameranın önündeki en fazla iki eli takip eder ve bir el açık → yumruk →
açık hareketini tamamladığında bir **sıkma** sayar. Her sıkma 50 Motion Energy
değerindedir; kodlama araları için kısa bir kan dolaşımı egzersizi gibi düşünün.

1. Taprivo'yu başlatın ve HUD'daki **Open Camera** düğmesine tıklayın (veya `uv run taprivo calibrate` çalıştırın).
2. Bir cihaz seçin. Siyah kare veren cihazlar (örneğin boşta duran iPhone
   Continuity Camera) *no signal* olarak işaretlenir.
3. **Start Camera** düğmesine tıklayın. macOS ilk seferde kamera izni ister.
4. **Calibrate** düğmesine tıklayıp yönergeleri izleyin: elinizi gösterin,
   iyice açın, yumruk yapın, sonra beş kez sıkın. Sonucu bu oturum için uygulayın.

Kamera penceresi her el için Sol/Sağ açıklık göstergesi ve kalibre edilmiş
açık/kapalı seviyelerini çentik olarak gösterir. Kamera kareleri bellekte
işlenir ve yalnızca Kamera penceresinde gösterilir; hiçbir zaman kaydedilmez,
loglanmaz ya da MCP üzerinden dışa açılmaz. Kalibrasyon verisi, ayar için el
başına özelliklerden oluşan bir CSV (görüntü ve ham landmark içermez) olarak
dışa aktarılabilir.

Bilinen sınırlamalar: iyi aydınlatma ve elin tamamının karede olması gerekir;
çok hızlı sıkmalar sayılmaz; eller görüş alanındayken klavye kullanmak ara sıra
sıkma olarak sayılabilir. Parmak vuruşları kamerayla algılanmaz; bunun için
klavye simülatörünü kullanın. `taprivo doctor --camera-probe` kamerayı açarak izni denetler ve işlenmiş fps
değerini raporlar.

## CLI

| Komut | İşlev |
|---|---|
| `taprivo` | HUD'u aç |
| `taprivo --version` | Sürümü yazdır |
| `taprivo simulate` | HUD'u klavye simülatörü açık olarak başlat |
| `taprivo camera list [--json]` | Kamera cihazlarını listele |
| `taprivo calibrate` | Kalibrasyon için Kamera penceresini aç |
| `taprivo status [--json]` | Çalışan uygulamanın bakiyesi, takip durumu, kamera fps'i ve uç noktası |
| `taprivo stats [--json]` | Oturum istatistikleri |
| `taprivo setup claude [--project] [--install-instructions] [--dry-run]` | Claude Code'u bağla |
| `taprivo remove claude [--project]` | Claude Code bağlantısını kaldır |
| `taprivo doctor [--json] [--camera-probe]` | Yerel kurulumu teşhis et; prob kamerayı açar |

Çıkış kodları: 0 başarı, 1 işlem hatası, 2 geçersiz argüman veya yapılandırma.

## Yapılandırma

Kullanıcı ayarları `~/.config/taprivo/config.yaml` dosyasında tutulur ve paketle
gelen varsayılanlarla (`src/taprivo/resources/default.yaml`) birleştirilir; kullanıcı değerleri öncelik kazanır.

```yaml
energy:
  energy_per_tap: 10
  max_energy: 10000
server:
  port: 32145
hud:
  always_on_top: true
  opacity: 0.92
  reduced_motion: false
  theme: system   # system | dark | light
camera:
  device_index: null   # null = Kamera penceresinde seçilir
  width: 640
  height: 480
squeeze:
  open_level: 0.80     # kalibrasyon iki seviyeyi de oturum boyunca geçersiz kılar
  closed_level: 0.45
```

32145 portu doluysa HUD `MCP: Error` gösterir; `server.port` değerini değiştirip
`taprivo setup claude` komutunu yeniden çalıştırın. Taprivo portu sessizce
değiştirmez.

## Gizlilik ve güvenlik

- MCP sunucusu yalnızca loopback'e bağlanır, `Host` ve `Origin` başlıklarını
  denetler ve `~/.config/taprivo/token` (0600) dosyasındaki rastgele kullanıcı
  token'ını ister. CORS başlığı gönderilmez.
- Kamera kareleri bellekte işlenir; kaydedilmez, yüklenmez, MCP'ye açılmaz. El
  takibi modeli cihazda çalışır; mediapipe, kullanım kaydı içermeyen bir
  sürüme sabitlenmiştir.
- Telemetri yoktur. Tek ağ dinleyicisi yerel uç noktadır.
- Loglar düşük hacimlidir; token'ı hiçbir zaman, harcama gerekçelerini ise tam metin olarak içermez.

## Mimari

```
Kamera (MediaPipe el landmark'ları, sıkma algılayıcı) / klavye simülatörü
  → TapEvent
  → EnergyEngine (tek kilit, atomik harcama, idempotency)
      ├─ HUD (PySide6)
      └─ MCP sunucusu (Streamable HTTP, loopback, bearer token)
           ├─ Claude Code projesi A
           └─ Claude Code projesi B
```

## Destek matrisi

| Alan | Durum |
|---|---|
| Klavye simülatörü | uygulandı |
| HUD | uygulandı |
| MCP araçları ve Claude Code kurulumu | uygulandı |
| Kamerayla sıkma algılama (iki el) | uygulandı (beta) |
| Kalibrasyon | uygulandı (beta) |
| Ritim, combo bonusları | planlandı |
| Kalıcı istatistikler (SQLite) | planlandı |
| Diğer ajanlar (Cursor, Codex, …) | planlandı |

Bkz. [ROADMAP.md](ROADMAP.md).

## Katkı

Simülatörle başlayın; katkı için kamera gerekmez. [CONTRIBUTING.md](CONTRIBUTING.md)
dosyasını okuyun, bir `good first issue` seçin ve PR açın.

## Lisans

Apache-2.0. Bkz. [LICENSE](LICENSE) ve [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
