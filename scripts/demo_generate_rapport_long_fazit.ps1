param(
  [string]$ApiKey = "dev-secret",
  [int]$Port = 8080,
  [string]$ImageName = "gpt-pdf-generator:rapport-long-fazit-demo",
  [string]$OutPdf = "..\rapport_test_long_fazit.pdf"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
  Write-Host ""
  Write-Host "==> $msg"
}

$longFazit = @"
Kurze Zusammenfassung:
- Release bleibt planmäßig.
- Offene Punkte wurden priorisiert.
- Nächste Schritte sind definiert.

Details:
Im heutigen Meeting wurden die wichtigsten Fortschritte der letzten Woche zusammengetragen. Das Team hat die aktuellen Risiken bewertet und konkrete Gegenmaßnahmen beschlossen. Besonders relevant sind dabei die Abhängigkeiten zu externen Schnittstellen sowie die Stabilität der CI.

Blocker und Risiken:
Der Import-Job muss unter Last getestet werden. Zusätzlich wird ein Monitoring für Fehlerraten und Latenzen ergänzt. Falls die externe API Rate-Limits verschärft, wird ein lokaler Cache aktiviert und die Batch-Größe automatisch reduziert.

Maßnahmen:
- Monitoring-Dashboard ergänzen (Fehlerrate, Latenz, Queue-Länge)
- Fallback-Strategie dokumentieren und testen
- Verantwortlichkeiten pro Teilbereich klar zuweisen
- Terminplan mit Puffer aktualisieren

Zusatz (lange Passage zum Test der Pagination):
Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed non risus. Suspendisse lectus tortor, dignissim sit amet, adipiscing nec, ultricies sed, dolor. Cras elementum ultrices diam. Maecenas ligula massa, varius a, semper congue, euismod non, mi. Proin porttitor, orci nec nonummy molestie, enim est eleifend mi, non fermentum diam nisl sit amet erat. Duis semper. Duis arcu massa, scelerisque vitae, consequat in, pretium a, enim. Pellentesque congue. Praesent dapibus, neque id cursus faucibus, tortor neque egestas augue, eu vulputate magna eros eu erat. Aliquam erat volutpat. Nam dui mi, tincidunt quis, accumsan porttitor, facilisis luctus, metus. Phasellus ultrices nulla quis nibh. Quisque a lectus. Donec consectetuer ligula vulputate sem tristique cursus. Nam nulla quam, gravida non, commodo a, sodales sit amet, nisi. Pellentesque fermentum dolor. Aliquam quam lectus, facilisis auctor, ultrices ut, elementum vulputate, nunc.

Noch ein Absatz:
Integer tincidunt. Cras dapibus. Vivamus elementum semper nisi. Aenean vulputate eleifend tellus. Aenean leo ligula, porttitor eu, consequat vitae, eleifend ac, enim. Aliquam lorem ante, dapibus in, viverra quis, feugiat a, tellus. Phasellus viverra nulla ut metus varius laoreet. Quisque rutrum. Aenean imperdiet. Etiam ultricies nisi vel augue. Curabitur ullamcorper ultricies nisi. Nam eget dui.
"@

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

  Write-Step "Call /generate (template=rapport, long fazit)"
  $payload = [ordered]@{
    title    = "RAPPORT"
    subtitle = "Meeting-Rapport (Type-C Look)"
    template = "rapport"
    content  = ""
    rapport  = [ordered]@{
      datum = "13.03.2026"
      meeting_titel = "Weekly Meeting - Projektstatus und Entscheidungen"
      moderator = "Elias"
      teilnehmer = @("Elias", "Max", "Sara", "Luca")
      besprochene_themen = @(
        @{ thema = "Projektstatus"; beschreibung = "Kurzupdate zu Fortschritt, Blockern und nächstem Release-Fenster."; ergebnis = "Release bleibt planmäßig, Blocker wird bis Dienstag gelöst." }
      )
      aufgabenbeurteilung = @(
        @{ aufgabe = "CI Pipeline stabilisieren"; verantwortlich = "Max"; status = "In Arbeit"; bewertung = "Flaky Tests reduziert, noch 2 Jobs instabil." }
      )
      neue_aufgaben = @(
        @{ neue_aufgabe = "Rapport-Template integrieren"; verantwortlich = "Elias"; prioritaet = "Hoch"; faellig_bis = "18.03.2026" }
      )
      wichtige_entscheidungen = @(
        @{ entscheidung = "Template-Standard"; hintergrund = "Einheitliches Branding über alle PDF-Typen."; verantwortlich = "Elias" }
      )
      fazit = $longFazit
    }
  }

  $body = $payload | ConvertTo-Json -Depth 12
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

