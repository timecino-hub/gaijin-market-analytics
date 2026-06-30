param(
  [Parameter(Mandatory=$true)][string]$InputJson,
  [Parameter(Mandatory=$true)][string]$OutputJson
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null

function AwaitOperation($WinRtTask, $ResultType) {
  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
      $_.Name -eq 'AsTask' -and
      $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } |
    Select-Object -First 1).MakeGenericMethod($ResultType)
  $netTask = $asTaskGeneric.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  return $netTask.Result
}

function BoxToHash($Rect) {
  return @{
    x = [string][decimal]$Rect.X
    y = [string][decimal]$Rect.Y
    width = [string][decimal]$Rect.Width
    height = [string][decimal]$Rect.Height
  }
}

function UnionBoxes($Boxes) {
  if ($null -eq $Boxes -or $Boxes.Count -eq 0) {
    return $null
  }
  $left = [double]::PositiveInfinity
  $top = [double]::PositiveInfinity
  $right = [double]::NegativeInfinity
  $bottom = [double]::NegativeInfinity
  foreach ($box in $Boxes) {
    if ($box.X -lt $left) { $left = $box.X }
    if ($box.Y -lt $top) { $top = $box.Y }
    if (($box.X + $box.Width) -gt $right) { $right = $box.X + $box.Width }
    if (($box.Y + $box.Height) -gt $bottom) { $bottom = $box.Y + $box.Height }
  }
  return @{
    x = [string][decimal]$left
    y = [string][decimal]$top
    width = [string][decimal]($right - $left)
    height = [string][decimal]($bottom - $top)
  }
}

function RecognizeImage($Path) {
  $file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
  $stream = AwaitOperation ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
  $decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($null -eq $engine) {
    throw "Windows OCR engine is unavailable for the current user profile languages."
  }
  $result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $lines = New-Object System.Collections.Generic.List[object]
  $lineOrder = 0
  foreach ($line in $result.Lines) {
    $words = New-Object System.Collections.Generic.List[object]
    $wordBoxes = New-Object System.Collections.Generic.List[object]
    $wordOrder = 0
    foreach ($word in $line.Words) {
      $wordBox = BoxToHash $word.BoundingRect
      $wordBoxes.Add($word.BoundingRect)
      $words.Add(@{
        text = [string]$word.Text
        order = $wordOrder
        bounding_box = $wordBox
      })
      $wordOrder += 1
    }
    $lines.Add(@{
      text = [string]$line.Text
      order = $lineOrder
      bounding_box = UnionBoxes -Boxes $wordBoxes.ToArray()
      words = @($words.ToArray())
    })
    $lineOrder += 1
  }
  return @{
    text = [string]$result.Text
    lines = @($lines.ToArray())
  }
}

$inputPayload = Get-Content -Raw -Encoding UTF8 $InputJson | ConvertFrom-Json
$imagePath = [string]$inputPayload.image_path
$debugDir = $inputPayload.debug_artifacts_dir
$source = [System.Drawing.Bitmap]::FromFile($imagePath)
$fields = @{}
$warnings = New-Object System.Collections.Generic.List[string]
$warnings.Add("ocr_confidence_unavailable")
$tempFiles = New-Object System.Collections.Generic.List[string]

try {
  foreach ($property in $inputPayload.rois.PSObject.Properties) {
    $fieldName = $property.Name
    $roi = $property.Value
    $x = [Math]::Floor([decimal]$roi.x * $source.Width)
    $y = [Math]::Floor([decimal]$roi.y * $source.Height)
    $w = [Math]::Max(1, [Math]::Floor([decimal]$roi.width * $source.Width))
    $h = [Math]::Max(1, [Math]::Floor([decimal]$roi.height * $source.Height))
    if ($x + $w -gt $source.Width) { $w = $source.Width - $x }
    if ($y + $h -gt $source.Height) { $h = $source.Height - $y }

    $scale = 3
    $crop = New-Object System.Drawing.Bitmap ($w * $scale), ($h * $scale)
    $graphics = [System.Drawing.Graphics]::FromImage($crop)
    $graphics.Clear([System.Drawing.Color]::White)
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $destRect = New-Object System.Drawing.Rectangle 0, 0, ($w * $scale), ($h * $scale)
    $srcRect = New-Object System.Drawing.Rectangle $x, $y, $w, $h
    $graphics.DrawImage($source, $destRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
    $graphics.Dispose()

    if ($debugDir) {
      [System.IO.Directory]::CreateDirectory([string]$debugDir) | Out-Null
      $cropPath = Join-Path ([string]$debugDir) ($fieldName + ".png")
    } else {
      $cropPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString("N") + ".png")
      $tempFiles.Add($cropPath)
    }
    $crop.Save($cropPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $crop.Dispose()
    $recognized = RecognizeImage $cropPath
    $fields[$fieldName] = @{
      raw_text = $recognized.text
      confidence = $null
      confidence_source = "unavailable"
      bounding_box = @{
        x = "0"
        y = "0"
        width = [string][decimal]($w * $scale)
        height = [string][decimal]($h * $scale)
      }
      lines = @($recognized.lines)
      warnings = @("ocr_confidence_unavailable")
    }
  }
}
finally {
  $source.Dispose()
  foreach ($tempFile in $tempFiles) {
    if (Test-Path $tempFile) {
      Remove-Item -LiteralPath $tempFile -Force
    }
  }
}

$output = @{
  fields = $fields
  warnings = @($warnings | Select-Object -Unique)
}
$output | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputJson
