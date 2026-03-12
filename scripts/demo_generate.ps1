param(
  [string]$ApiKey = "dev-secret",
  [int]$Port = 8080,
  [string]$ImageName = "gpt-pdf-generator:demo",
  [string]$OutPdf = "demo_output.pdf",
  [string]$RequestPath = ""
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
  Write-Host ""
  Write-Host "==> $msg"
}

Write-Step "Build Docker image ($ImageName)"
docker build -t $ImageName .

Write-Step "Start container on http://localhost:$Port"
$cid = docker run -d --rm `
  -e PORT=8080 `
  -e PDF_API_KEY=$ApiKey `
  -p "$Port`:8080" `
  $ImageName

try {
  Write-Step "Wait for /health"
  $healthUrl = "http://localhost:$Port/health"
  $ok = $false
  for ($i=0; $i -lt 40; $i++) {
    try {
      $resp = Invoke-RestMethod -Method GET -Uri $healthUrl -TimeoutSec 2
      if ($resp.ok -eq $true) { $ok = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 400
  }
  if (-not $ok) { throw "Service did not become healthy on $healthUrl" }

  Write-Step "Call /generate with demo content"
  if ($RequestPath -and (Test-Path -LiteralPath $RequestPath)) {
    $body = Get-Content -Raw -LiteralPath $RequestPath -Encoding UTF8
  } else {
    $demoContent = @"
# 1. Einleitung und Zielsetzung - Vision, Struktur & Governance

Dies ist der Intro-Leadtext (kommt in den oberen Intro-Textblock). Er ist absichtlich kurz, damit der restliche Inhalt in die linke Spalte wandert und bei Bedarf auf Continue-Seiten umbricht.

## Vision, Organisationsstruktur, Governance und langfristige Entwicklungsstrategie

Dieser Untertitel ist **kapitelspezifisch** und muss direkt **unter dem orangenen Titel** stehen (nicht hinter/unter dem blauen Container).

Die GPS Group versteht sich als moderne Holdingstruktur, die mehrere operative Geschaeftsbereiche unter einer gemeinsamen strategischen Fuehrung vereint. Ziel ist es, nachhaltiges Wachstum zu foerdern, Innovation zu ermoeglichen und eine stabile organisatorische Grundlage fuer zukuenftige Projekte zu schaffen.

Dieses Strategiepapier verfolgt mehrere zentrale Zwecke (als Liste).

- Definition der langfristigen Vision der Gruppe
- Festlegung der organisatorischen Struktur
- Klärung von Rollen, Verantwortlichkeiten und Governance
- Entwicklung strategischer Leitlinien für Wachstum und Innovation
- (Extra Punkt) Dieser Punkt ist absichtlich lang, damit man sieht, wie sauber der Text in den blauen Container gekürzt wird, ohne überzulaufen oder das Layout zu zerstören.

Darueber hinaus dient das Dokument als Referenzrahmen fuer zukuenftige Entscheidungen, Investitionen und Kooperationen. Um die Continue-Logik zu testen, folgen nun mehrere Absaetze mit bewusst viel Text.

Absatz A: Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed non risus. Suspendisse lectus tortor, dignissim sit amet, adipiscing nec, ultricies sed, dolor. Cras elementum ultrices diam. Maecenas ligula massa, varius a, semper congue, euismod non, mi.

Absatz B: Proin porttitor, orci nec nonummy molestie, enim est eleifend mi, non fermentum diam nisl sit amet erat. Duis semper. Duis arcu massa, scelerisque vitae, consequat in, pretium a, enim. Pellentesque congue.

Absatz C: Praesent dapibus, neque id cursus faucibus, tortor neque egestas augue, eu vulputate magna eros eu erat. Aliquam erat volutpat. Nam dui mi, tincidunt quis, accumsan porttitor, facilisis luctus, metus.

Absatz D: Phasellus ultrices nulla quis nibh. Quisque a lectus. Donec consectetuer ligula vulputate sem tristique cursus. Nam nulla quam, gravida non, commodo a, sodales sit amet, nisi.

Absatz E: Pellentesque fermentum dolor. Aliquam quam lectus, facilisis auctor, ultrices ut, elementum vulputate, nunc.

Absatz F: Donec at pede. Etiam vel neque nec dui dignissim bibendum. Vivamus id enim. Phasellus neque orci, porta a, aliquet quis, semper a, massa.

Absatz G: Integer tincidunt. Cras dapibus. Vivamus elementum semper nisi. Aenean vulputate eleifend tellus. Aenean leo ligula, porttitor eu, consequat vitae, eleifend ac, enim.

Absatz H: Aliquam lorem ante, dapibus in, viverra quis, feugiat a, tellus. Phasellus viverra nulla ut metus varius laoreet. Quisque rutrum.

Absatz I: Aenean imperdiet. Etiam ultricies nisi vel augue. Curabitur ullamcorper ultricies nisi. Nam eget dui.

Absatz J: Etiam rhoncus. Maecenas tempus, tellus eget condimentum rhoncus, sem quam semper libero, sit amet adipiscing sem neque sed ipsum.

Absatz K: Nam quam nunc, blandit vel, luctus pulvinar, hendrerit id, lorem. Maecenas nec odio et ante tincidunt tempus.

Absatz L: Donec vitae sapien ut libero venenatis faucibus. Nullam quis ante. Etiam sit amet orci eget eros faucibus tincidunt. Duis leo.

Absatz M: Sed fringilla mauris sit amet nibh. Donec sodales sagittis magna. Sed consequat, leo eget bibendum sodales, augue velit cursus nunc.

Absatz N: Quisque rutrum. Aenean imperdiet. Etiam ultricies nisi vel augue. Curabitur ullamcorper ultricies nisi.

# 2. Vision und Mission der GPS Group

Dies ist der Intro-Leadtext von Kapitel 2 (oben im Intro-Textblock). Hier soll man sehen, dass das Overview-Icon-Label NICHT mehr "A BRIEF STORY ABOUT THE PRODUCT" ist, sondern sich pro Kapitel ändert.

## Plattform für Innovation und Synergien

Dieser Untertitel ist für Kapitel 2 anders und muss korrekt platziert sein.

Vision: Die GPS Group verfolgt die Vision, eine innovative und zukunftsorientierte Unternehmensgruppe aufzubauen, die durch strategische Zusammenarbeit, technologische Innovation und nachhaltiges Management langfristigen wirtschaftlichen Erfolg erzielt.

Mission: Die Mission der GPS Group besteht darin, nachhaltige Geschäftsmodelle aufzubauen, innovative Projekte zu fördern, effiziente Organisationsstrukturen zu schaffen und langfristige Wertschöpfung zu generieren.
"@

    $payload = [ordered]@{
      title    = "Demo Dokument - Type C Layouttest"
      subtitle = "Untertitel (Fallback) - sollte NICHT ueberall gleich sein"
      template = "document"
      content  = $demoContent
    }

    $body = $payload | ConvertTo-Json -Depth 6
  }
  $gen = Invoke-RestMethod -Method POST -Uri "http://localhost:$Port/generate" -ContentType "application/json; charset=utf-8" -Body $body

  if (-not $gen.url) { throw "No url returned from /generate" }
  Write-Host "PDF URL: $($gen.url)"

  Write-Step "Download PDF to $OutPdf"
  if (Test-Path -LiteralPath $OutPdf) {
    try { Remove-Item -LiteralPath $OutPdf -Force -ErrorAction Stop } catch {}
  }
  Invoke-WebRequest -Uri $gen.url -OutFile $OutPdf

  Write-Step "Done"
  Write-Host "Generated: $(Resolve-Path $OutPdf)"
}
finally {
  Write-Step "Stop container"
  docker stop $cid | Out-Null
}

