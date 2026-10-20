# Offline narration with the Windows speech engine (System.Speech); nothing leaves the machine.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/tts_windows.ps1 -Jobs jobs.json [-Voice "Microsoft Huihui Desktop"]
# jobs.json: [{"text": "...", "path": "C:\\...\\s00.wav"}, ...]; prints the voice that was used.
param([Parameter(Mandatory = $true)][string]$Jobs, [string]$Voice = '')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice) {
        $speaker.SelectVoice($Voice)
    } else {
        $chinese = $speaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN' } |
            Select-Object -First 1
        if (-not $chinese) { throw 'No enabled zh-CN voice is installed' }
        $speaker.SelectVoice($chinese.VoiceInfo.Name)
    }
    $speaker.Rate = 0
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($job in (Get-Content -Raw -Encoding UTF8 -LiteralPath $Jobs | ConvertFrom-Json)) {
        $speaker.SetOutputToWaveFile($job.path, $format)
        $speaker.Speak($job.text)
        $speaker.SetOutputToNull()
    }
    Write-Output $speaker.Voice.Name
} finally {
    $speaker.Dispose()
}
