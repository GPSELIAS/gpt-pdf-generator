param(
  [string]$ApiKey = "dev-secret",
  [int]$Port = 8080,
  [string]$ImageName = "gpt-pdf-generator:rapport-demo",
  [string]$OutPdf = "..\rapport_test.pdf"
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

  Write-Step "Call /generate (template=rapport)"
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
        @{ thema = "Projektstatus"; beschreibung = "Kurzupdate zu Fortschritt, Blockern und nächstem Release-Fenster."; ergebnis = "Release bleibt planmäßig, Blocker wird bis Dienstag gelöst." },
        @{ thema = "Risiken"; beschreibung = "Abhängigkeit von externem API-Limit; mögliche Verzögerung bei Datenimport."; ergebnis = "Fallback-Strategie beschlossen, Monitoring wird ergänzt." }
      )
      aufgabenbeurteilung = @(
        @{ aufgabe = "CI Pipeline stabilisieren"; verantwortlich = "Max"; status = "In Arbeit"; bewertung = "Flaky Tests reduziert, noch 2 Jobs instabil." },
        @{ aufgabe = "PDF Layout Review"; verantwortlich = "Sara"; status = "Erledigt"; bewertung = "Type C ist freigegeben, Rapport-Template kommt als nächstes." }
      )
      neue_aufgaben = @(
        @{ neue_aufgabe = "Rapport-Template integrieren"; verantwortlich = "Elias"; prioritaet = "Hoch"; faellig_bis = "18.03.2026" },
        @{ neue_aufgabe = "Tabellen-Datenpipeline definieren"; verantwortlich = "Luca"; prioritaet = "Mittel"; faellig_bis = "20.03.2026" }
      )
      wichtige_entscheidungen = @(
        @{ entscheidung = "Template-Standard"; hintergrund = "Einheitliches Branding über alle PDF-Typen."; verantwortlich = "Elias" },
        @{ entscheidung = "Spaltenbreiten fix"; hintergrund = "Stabile Zeilenumbrüche und Lesbarkeit im A3-Layout."; verantwortlich = "Max" }
      )
      fazit = "Kurze Zusammenfassung:\n- Release bleibt planmäßig.\n- Zwei neue Aufgaben wurden angelegt.\n- Risiken werden mit Fallback und Monitoring adressiert."
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

