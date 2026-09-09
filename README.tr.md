# Taprivo

**Kodunu hareketle güçlendir.** ⚡

[English README](README.md)

Taprivo, el hareketini AI kodlama ajanları için oyunlaştırılmış bir iş
bütçesine dönüştürür. Kameranın önünde ellerinizi sıkın (ya da simülatörde
tuşlara basın), Motion Energy biriktirin ve Claude Code bu bakiyeyi MCP
üzerinden okuyup harcasın. El takibi cihazınızda yerel olarak çalışır; kamera
kareleri cihazınızdan çıkmaz. Motion Energy bir oyun mekaniğidir; API token'ı
veya kredi değildir.

> **Durum: beta (0.1.0b5).** Kamerayla sıkma
> algılama ve kalibrasyon, klavye simülatörünün yanında kullanılabilir. Apple
> Silicon macOS üzerinde dahili FaceTime kamerayla test edilmiştir.

## Ne yapar?

- Her vuruş oturum bakiyesine 10 Motion Energy ekler (× combo çarpanı, en çok 2×; üst sınır 10.000); bir kamera sıkması beş vuruş sayılır (50 enerji). Temponuzu sabit tutarsanız vuruş başına enerji %25 daha artar.
- Küçük, her zaman üstte duran HUD (koyu veya açık tema) enerjiyi, her elin tuş sayımlarını, combo'yu ve çarpanını, hızı ve tempoyu, kamera ve MCP durumunu gösterir.
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

Taprivo kapanınca bakiye sıfırlanır ama oturum unutulmaz: her oturum ve günlük
toplamları yerel bir SQLite dosyasına yazılır; `taprivo stats --today` ve
`taprivo stats --history` uygulama kapalıyken de çalışır.

## Gereksinimler

- Apple Silicon macOS (diğer platformlar test edilmedi)
- Python 3.12 veya 3.13 ve [uv](https://docs.astral.sh/uv/)
- Ajan entegrasyonu için [Claude Code](https://code.claude.com)

## Kurulum

Size uyan yolu seçin; en kolayı uygulama.

### 1. macOS uygulaması (en kolay, Apple Silicon)

1. [Son sürümden](https://github.com/yorulmazsinan/taprivo/releases/latest)
   `Taprivo-<sürüm>.dmg` dosyasını indirin.
2. DMG'yi açıp **Taprivo**'yu **Applications** klasörüne sürükleyin. Uygulama
   Developer ID ile imzalı ve Apple tarafından notarize edilmiştir; ek adım
   gerekmeden açılır.
3. Taprivo'yu açın. HUD belirir; rakam sırasına vurun (sol el `1`–`4`, sağ el
   `7`–`0`) ve enerjinin birikmesini izleyin ya da el sıkmalarını saymak için
   **Open Camera** düğmesine tıklayın. macOS ilk seferde kamera izni ister.
4. HUD'daki **Setup…** düğmesiyle Claude Code veya Cursor'ı bağlayın ve
   doctor'ı çalıştırın; aynı pencere uygulamayla gelen komut satırı aracını da
   gösterir. Aynısını Terminal'den yapmak için:

   ```bash
   /Applications/Taprivo.app/Contents/MacOS/Taprivo setup claude --install-instructions
   /Applications/Taprivo.app/Contents/MacOS/Taprivo doctor
   ```

   Cursor için `setup cursor --install-instructions` kullanın. Çalışırken
   uygulamayı açık tutun; kapanınca bakiye sıfırlanır.

### 2. Komut satırı (uv veya pipx)

```bash
uv tool install taprivo            # ya da: pipx install taprivo
taprivo                            # HUD'u açar
taprivo setup claude --install-instructions
taprivo doctor
```

Python 3.12 veya 3.13 gerekir. Kurulum mediapipe, OpenCV ve Qt nedeniyle yaklaşık
1,5 GB yer kaplar. Güncellemek için `uv tool upgrade taprivo`, kaldırmak için
`uv tool uninstall taprivo`.

### 3. Kaynaktan (katkıcılar)

Aşağıdaki hızlı başlangıcı izleyin. Katkıcılar `packaging/macos/build.sh build`
ile uygulama paketini de üretebilir.

## Hızlı başlangıç (klonla)

```bash
git clone https://github.com/yorulmazsinan/taprivo.git
cd taprivo
uv sync
uv run taprivo simulate
```

HUD klavye modu açık olarak gelir; iki el de rakam sırasındadır. Sol el
`1` `2` `3` `4` (serçeden işarete), sağ el `7` `8` `9` `0` (işaretten
serçeye) tuşlarına vurur. Her basış 10 enerji ekler; vuruşlar kesilmezse
combo çarpanı 10. ardışık vuruşta 1,5×, 25. vuruşta 2× olur. Tempoyu 60-240
BPM arasında düzenli tutarsanız HUD tempoyu vurgular ve 1,25× daha ekler.
Saniyede 12'yi aşan vuruşlar sayılmaz; basılı tutulan tuş enerji kazandırmaz.

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

### Cursor

```bash
uv run taprivo setup cursor --install-instructions
uv run taprivo doctor
```

Taprivo global olarak kuruluysa `taprivo setup cursor --install-instructions`
komutunu kullanın. `setup cursor`, `~/.cursor/mcp.json` dosyasına bir `taprivo`
girdisi ekler (0600 kipi, önce yedek alınır) ve `--install-instructions` ile
`~/.cursor/taprivo.md` dosyasını yazar. Cursor'ın kullanıcı kuralları için bir
komutu yoktur; bu dosyanın içeriğini Settings > Rules bölümüne kendiniz
yapıştırın.

`--project` ile depo içine, token'ı `${env:TAPRIVO_TOKEN}` ortam değişkeninden
alan bir `.cursor/mcp.json` ve aynı bütçe kurallarını taşıyan
`.cursor/rules/taprivo.mdc` dosyasını da yazar. `--dry-run` hiçbir şey yazmadan
değişiklikleri gösterir; `uv run taprivo remove cursor` girdiyi ve Taprivo'nun
eklediği dosyaları kaldırır.

## Kamera

Taprivo kameranın önündeki en fazla iki eli takip eder ve bir el açık → yumruk →
açık hareketini tamamladığında bir **sıkma** sayar. Her sıkma 50 Motion Energy
değerindedir; kodlama araları için kısa bir kan dolaşımı egzersizi gibi düşünün.

1. Taprivo'yu başlatın ve HUD'daki **Open Camera** düğmesine tıklayın (veya `uv run taprivo calibrate` çalıştırın).
2. Bir cihaz seçin. Taprivo dahili kamerayı tercih eder; iPhone Continuity
   Camera listelenir ama siz seçmeden açılmaz (varsayılanı değiştirmek için
   `camera.prefer_builtin: false`).
3. **Start Camera** düğmesine tıklayın. macOS ilk seferde kamera izni ister.
4. **Calibrate** düğmesine tıklayıp yönergeleri izleyin: elinizi gösterin,
   iyice açın, yumruk yapın, sonra beş kez sıkın. Sonucu bu oturum için uygulayın.

Kamera penceresini kapatmak kamerayı durdurur; sıkmalardan gelen enerji HUD'da kalır.

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
Kapağı kapalı bir MacBook'ta dahili kamera görüntü vermez; kapağı açın ya da başka bir kamera seçin.
Elin tamamını kadrajda tutun; kısmen görünen el yok sayılır.

## CLI

| Komut | İşlev |
|---|---|
| `taprivo` | HUD'u aç |
| `taprivo --version` | Sürümü yazdır |
| `taprivo simulate` | HUD'u klavye modu açık olarak başlat |
| `taprivo camera list [--json]` | Kamera cihazlarını listele |
| `taprivo calibrate` | Kalibrasyon için Kamera penceresini aç |
| `taprivo status [--json]` | Çalışan uygulamanın bakiyesi, takip durumu, kamera fps'i ve uç noktası |
| `taprivo stats [--json]` | Çalışan uygulamanın oturum istatistikleri ve bugünün toplamları |
| `taprivo stats --today [--json]` | Bugünün toplamları, yerel istatistik dosyasından |
| `taprivo stats --history [--days N] [--json]` | Günlük toplamlar, en yeniden eskiye (varsayılan 30 gün) |
| `taprivo setup claude [--project] [--install-instructions] [--dry-run] [--json]` | Claude Code'u bağla |
| `taprivo remove claude [--project] [--json]` | Claude Code bağlantısını kaldır |
| `taprivo setup cursor [--project] [--install-instructions] [--dry-run] [--json]` | Cursor'ı bağla |
| `taprivo remove cursor [--project] [--json]` | Cursor bağlantısını kaldır |
| `taprivo doctor [--json] [--camera-probe]` | Yerel kurulumu teşhis et; kamera cihazlarını listeler (iPhone Continuity Camera dışında, sinyali görmek için her indeksi kısaca açar); prob ayrıca izni denetler ve fps ölçer |

Çıkış kodları: 0 başarı, 1 işlem hatası, 2 geçersiz argüman veya yapılandırma.

## Yapılandırma

Kullanıcı ayarları `~/.config/taprivo/config.yaml` dosyasında tutulur ve paketle
gelen varsayılanlarla (`src/taprivo/resources/default.yaml`) birleştirilir; kullanıcı değerleri öncelik kazanır.

```yaml
energy:
  energy_per_tap: 10
  max_energy: 10000
combo:
  energy_multiplier_enabled: true
  tiers:            # combo kademeleri: ardışık vuruş → enerji çarpanı
    - {at: 10, multiplier: 1.5}
    - {at: 25, multiplier: 2.0}
rhythm:
  enabled: true
  steady_multiplier: 1.25   # düzenli tempo bonusu, combo ile çarpılır
server:
  port: 32145
simulator:
  max_taps_per_second: 12   # iki eli birlikte sayan bir saniyelik kayan sınır
hud:
  always_on_top: true
  opacity: 0.92
  reduced_motion: false
  theme: system   # system | dark | light
camera:
  device_index: null   # null = Kamera penceresinde seçilir
  prefer_builtin: true # false = sinyal veren ilk kamerayı seç
  width: 640
  height: 480
squeeze:
  open_level: 0.80     # kalibrasyon iki seviyeyi de oturum boyunca geçersiz kılar
  closed_level: 0.45
stats:
  enabled: true        # oturum ve günlük toplamlar; gerekçe ve görüntü yok
  path: null           # null = ~/.config/taprivo/stats.sqlite
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
- İstatistikler yalnızca sayaç ve zaman damgası olarak
  `~/.config/taprivo/stats.sqlite` dosyasında yerel tutulur — harcama gerekçesi
  ve kamera verisi yoktur; `stats.enabled: false` ile kapatılır.
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
| Klavyeyle davul (iki el, combo çarpanı) | uygulandı |
| HUD | uygulandı |
| MCP araçları ve Claude Code kurulumu | uygulandı |
| Kamerayla sıkma algılama (iki el) | uygulandı (beta) |
| Kalibrasyon | uygulandı (beta) |
| Ritim / BPM | uygulandı (beta) |
| Kalıcı istatistikler (SQLite) | uygulandı (beta) |
| Cursor | uygulandı (beta) |
| Diğer ajanlar (Codex, …) | planlandı |

Bkz. [ROADMAP.md](ROADMAP.md).

## Katkı

Simülatörle başlayın; katkı için kamera gerekmez. [CONTRIBUTING.md](CONTRIBUTING.md)
dosyasını okuyun, bir `good first issue` seçin ve PR açın.

## Lisans

Apache-2.0. Bkz. [LICENSE](LICENSE) ve [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
