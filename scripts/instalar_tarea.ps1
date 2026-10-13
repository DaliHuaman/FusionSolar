# Registra la tarea programada "FusionSolar Indicadores" (cada 30 min). Ejecutar en PowerShell normal (no requiere admin).
$ErrorActionPreference = "Stop"
$raiz   = Split-Path -Parent $PSScriptRoot
$accion = New-ScheduledTaskAction -Execute (Join-Path $PSScriptRoot "run_collector.bat") -WorkingDirectory $raiz
$inicio = (Get-Date).Date.AddMinutes([math]::Ceiling((Get-Date).TimeOfDay.TotalMinutes / 30) * 30)   # proxima hora :00 o :30
$disp   = New-ScheduledTaskTrigger -Once -At $inicio -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$conf   = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
          -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FusionSolar Indicadores" -Action $accion -Trigger $disp -Settings $conf `
          -Description "Extrae aislamiento y potencia activa de inversores, actualiza Excel y dashboard." -Force | Out-Null
Write-Host "Tarea creada. Primera ejecucion: $inicio. Para probarla ya: Start-ScheduledTask -TaskName 'FusionSolar Indicadores'"
