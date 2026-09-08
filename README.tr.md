# Taprivo

**Kodunu hareketle güçlendir.** ⚡

[English README](README.md)

Taprivo, parmak vuruşlarını AI kodlama ajanları için oyunlaştırılmış bir iş
bütçesine dönüştürür. Parmaklarınızla vurun, Motion Energy biriktirin ve
Claude Code bu bakiyeyi MCP üzerinden okuyup harcasın. El takibi cihazınızda
yerel olarak çalışacak; bu sürüm klavye simülatörünü içerir. Kamera kareleri
cihazınızdan çıkmaz. Motion Energy bir oyun mekaniğidir; API token'ı veya
kredi değildir.

> **Durum: alfa (0.1.0a1).** Bu sürüm kamerasız klavye simülatörünü, HUD'u ve
> MCP sunucusunu içerir. Kamerayla vuruş algılama geliştirme aşamasındadır.
> Apple Silicon macOS üzerinde test edilmiştir.

## Ne yapar?

- Her geçerli vuruş oturum bakiyesine 10 Motion Energy ekler (üst sınır 10.000).
- Küçük, her zaman üstte duran HUD enerjiyi, parmak sayımlarını, combo ve hızı gösterir.
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

## CLI

| Komut | İşlev |
|---|---|
| `taprivo` | HUD'u aç |
| `taprivo --version` | Sürümü yazdır |
| `taprivo simulate` | HUD'u klavye simülatörü açık olarak başlat |
| `taprivo status [--json]` | Çalışan uygulamanın bakiyesi, takip durumu ve uç noktası |
| `taprivo stats [--json]` | Oturum istatistikleri |
| `taprivo setup claude [--project] [--install-instructions] [--dry-run]` | Claude Code'u bağla |
| `taprivo remove claude [--project]` | Claude Code bağlantısını kaldır |
| `taprivo doctor [--json]` | Yerel kurulumu teşhis et |

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
```

32145 portu doluysa HUD `MCP: Error` gösterir; `server.port` değerini değiştirip
`taprivo setup claude` komutunu yeniden çalıştırın. Taprivo portu sessizce
değiştirmez.

## Gizlilik ve güvenlik

- MCP sunucusu yalnızca loopback'e bağlanır, `Host` ve `Origin` başlıklarını
  denetler ve `~/.config/taprivo/token` (0600) dosyasındaki rastgele kullanıcı
  token'ını ister. CORS başlığı gönderilmez.
- Kamera kareleri (kamera desteği geldiğinde) bellekte işlenir; kaydedilmez,
  yüklenmez, MCP'ye açılmaz.
- Telemetri yoktur. Tek ağ dinleyicisi yerel uç noktadır.
- Loglar düşük hacimlidir; token'ı hiçbir zaman, harcama gerekçelerini ise tam metin olarak içermez.

## Mimari

```
Klavye simülatörü / (planlanan) kamera
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
| Kamerayla vuruş algılama | geliştiriliyor |
| İki el, ritim, combo bonusları | planlandı |
| Kalıcı istatistikler (SQLite) | planlandı |
| Diğer ajanlar (Cursor, Codex, …) | planlandı |

Bkz. [ROADMAP.md](ROADMAP.md).

## Katkı

Simülatörle başlayın; katkı için kamera gerekmez. [CONTRIBUTING.md](CONTRIBUTING.md)
dosyasını okuyun, bir `good first issue` seçin ve PR açın.

## Lisans

Apache-2.0. Bkz. [LICENSE](LICENSE) ve [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
