# Draw the widget picker pictures for the small one-thing widgets, in the
# style of assets\WidgetScreenshot.png (dark card, Segoe UI). Run by hand
# after changing a small card:  powershell -File packaging\widget_shots.ps1
# Weather and air use Noida's reading of 2026-10-20; the rest are examples.
Add-Type -AssemblyName System.Drawing
$out = Join-Path $PSScriptRoot "assets"
$shots = [ordered]@{   # name = rows of text, size, bold, subtle
    Weather = @(@("$([char]0x2600) 22$([char]0xB0)", 48, $true, $false), @("Clear", 19, $false, $false),
                @("H 30$([char]0xB0) $([char]0xB7) L 19$([char]0xB0)", 16, $false, $true), @("Noida", 16, $false, $true))
    Air     = @(@("Air quality", 15, $false, $true), @("230", 48, $true, $false),
                @("Very unhealthy", 19, $false, $false), @("Noida", 16, $false, $true))
    Meeting = @(@("Next meeting", 15, $false, $true), @("15:00", 48, $true, $false),
                @("in 40 min", 19, $false, $false), @("Meeting", 16, $false, $true))
    Tasks   = @(@("Tasks", 15, $false, $true), @("3", 48, $true, $false), @("tasks open", 19, $false, $false))
    Focus   = @(@("Focus", 15, $false, $true), @("25", 48, $true, $false), @("min left", 19, $false, $false))
}
foreach ($name in $shots.Keys) {
    $bmp = New-Object System.Drawing.Bitmap 340, 340
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = "AntiAlias"; $g.TextRenderingHint = "AntiAliasGridFit"
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $r = 28; $path.AddArc(0, 0, $r, $r, 180, 90); $path.AddArc(339 - $r, 0, $r, $r, 270, 90)
    $path.AddArc(339 - $r, 339 - $r, $r, $r, 0, 90); $path.AddArc(0, 339 - $r, $r, $r, 90, 90); $path.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(43, 43, 43))), $path)
    $y = 40
    foreach ($row in $shots[$name]) {
        $text, $size, $bold, $subtle = $row
        $font = New-Object System.Drawing.Font "Segoe UI", $size, $(if ($bold) { "Bold" } else { "Regular" }), "Pixel"
        $c = if ($subtle) { 170 } else { 240 }
        $g.DrawString($text, $font, (New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($c, $c, $c))), 32, $y)
        $y += [int]($size * 1.45)
    }
    $bmp.Save((Join-Path $out "Widget$name.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
}
"drew " + ($shots.Keys -join ", ")
