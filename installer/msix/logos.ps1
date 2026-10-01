# MSIX trial: the package logos, drawn like the Thock mark (four white bars on a black rounded square).
param([string]$Out)
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force $Out | Out-Null
foreach ($logo in @(@{Name = "Square44x44Logo.png"; Size = 44}, @{Name = "Square150x150Logo.png"; Size = 150},
                    @{Name = "StoreLogo.png"; Size = 50})) {
  $s = $logo.Size
  $bmp = New-Object System.Drawing.Bitmap $s, $s
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.Clear([System.Drawing.Color]::Transparent)
  $r = [int]($s * 0.22)
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddArc(0, 0, 2 * $r, 2 * $r, 180, 90); $path.AddArc($s - 2 * $r - 1, 0, 2 * $r, 2 * $r, 270, 90)
  $path.AddArc($s - 2 * $r - 1, $s - 2 * $r - 1, 2 * $r, 2 * $r, 0, 90); $path.AddArc(0, $s - 2 * $r - 1, 2 * $r, 2 * $r, 90, 90)
  $path.CloseFigure()
  $g.FillPath((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(22, 22, 26))), $path)
  $white = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
  $bar = $s * 0.075; $gap = $s * 0.06
  $left = ($s - (4 * $bar + 3 * $gap)) / 2
  $heights = @(0.24, 0.44, 0.54, 0.34)
  for ($i = 0; $i -lt 4; $i++) {
    $h = $s * $heights[$i]
    $g.FillRectangle($white, [single]($left + $i * ($bar + $gap)), [single](($s - $h) / 2), [single]$bar, [single]$h)
  }
  $bmp.Save((Join-Path $Out $logo.Name), [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
}
