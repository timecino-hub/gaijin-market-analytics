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

function GetPreprocessingVariants($Payload) {
  $variants = @()
  if ($Payload.preprocessing -and $Payload.preprocessing.variants) {
    foreach ($variant in $Payload.preprocessing.variants) {
      $variants += $variant
    }
  }
  if ($variants.Count -eq 0) {
    $variants += [pscustomobject]@{
      name = "gray_3x"
      scale_factor = 3
      grayscale = $true
      autocontrast = $false
      sharpen = $false
      binary_threshold = $null
      invert = $false
    }
  }
  return @($variants | Select-Object -First 5)
}

function NewCropBitmap($Source, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Variant) {
  $scale = [int]$Variant.scale_factor
  if ($scale -lt 1) { $scale = 1 }
  $targetWidth = [Math]::Max(1, $Width * $scale)
  $targetHeight = [Math]::Max(1, $Height * $scale)
  $maxPixels = 2000000
  if ($targetWidth * $targetHeight -gt $maxPixels) {
    $ratio = [Math]::Sqrt($maxPixels / ($targetWidth * $targetHeight))
    $targetWidth = [Math]::Max(1, [int][Math]::Floor($targetWidth * $ratio))
    $targetHeight = [Math]::Max(1, [int][Math]::Floor($targetHeight * $ratio))
  }
  $crop = New-Object System.Drawing.Bitmap $targetWidth, $targetHeight
  $graphics = [System.Drawing.Graphics]::FromImage($crop)
  $graphics.Clear([System.Drawing.Color]::White)
  $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $destRect = New-Object System.Drawing.Rectangle 0, 0, $targetWidth, $targetHeight
  $srcRect = New-Object System.Drawing.Rectangle $X, $Y, $Width, $Height
  $graphics.DrawImage($Source, $destRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
  $graphics.Dispose()
  ApplyPixelPreprocessing $crop $Variant
  return $crop
}

function ApplyPixelPreprocessing($Bitmap, $Variant) {
  $needsPixels = [bool]$Variant.autocontrast -or $null -ne $Variant.binary_threshold -or [bool]$Variant.invert
  if (-not $needsPixels) {
    return
  }
  $threshold = $null
  if ($null -ne $Variant.binary_threshold) {
    $threshold = [int]$Variant.binary_threshold
  }
  $minGray = 255
  $maxGray = 0
  if ([bool]$Variant.autocontrast) {
    for ($py = 0; $py -lt $Bitmap.Height; $py++) {
      for ($px = 0; $px -lt $Bitmap.Width; $px++) {
        $color = $Bitmap.GetPixel($px, $py)
        $gray = [int](($color.R * 0.299) + ($color.G * 0.587) + ($color.B * 0.114))
        if ($gray -lt $minGray) { $minGray = $gray }
        if ($gray -gt $maxGray) { $maxGray = $gray }
      }
    }
  }
  for ($py = 0; $py -lt $Bitmap.Height; $py++) {
    for ($px = 0; $px -lt $Bitmap.Width; $px++) {
      $color = $Bitmap.GetPixel($px, $py)
      $gray = [int](($color.R * 0.299) + ($color.G * 0.587) + ($color.B * 0.114))
      if ([bool]$Variant.autocontrast -and $maxGray -gt $minGray) {
        $gray = [int][Math]::Round((($gray - $minGray) * 255.0) / ($maxGray - $minGray))
      }
      if ($null -ne $threshold) {
        if ($gray -ge $threshold) { $gray = 255 } else { $gray = 0 }
      }
      if ([bool]$Variant.invert) {
        $gray = 255 - $gray
      }
      $Bitmap.SetPixel($px, $py, [System.Drawing.Color]::FromArgb($gray, $gray, $gray))
    }
  }
}

function RegionHasInk($Bitmap, [int]$X, [int]$Y, [int]$Width, [int]$Height) {
  $stepX = [Math]::Max(1, [int][Math]::Floor($Width / 32))
  $stepY = [Math]::Max(1, [int][Math]::Floor($Height / 32))
  $right = [Math]::Min($Bitmap.Width, $X + $Width)
  $bottom = [Math]::Min($Bitmap.Height, $Y + $Height)
  for ($py = $Y; $py -lt $bottom; $py += $stepY) {
    for ($px = $X; $px -lt $right; $px += $stepX) {
      $color = $Bitmap.GetPixel($px, $py)
      if ($color.R -lt 245 -or $color.G -lt 245 -or $color.B -lt 245) {
        return $true
      }
    }
  }
  return $false
}

function ScoreRecognizedText([string]$FieldName, [string]$Text) {
  $clean = if ($Text) { $Text.Trim() } else { "" }
  if ($clean.Length -eq 0) {
    return 0
  }
  $score = 10
  if ($FieldName -match "bid|ask|price|levels") {
    if ($clean -match "\d+[\.,]\d{1,2}") { $score += 40 }
    elseif ($clean -match "\d+") { $score += 20 }
    if ($clean -match "[A-Za-z\u4e00-\u9fff]") { $score -= 5 }
  } elseif ($FieldName -match "quantity") {
    if ($clean -match "\b\d+\b") { $score += 30 }
    if ($clean -match "\d+[\.,]\d+") { $score -= 15 }
  } elseif ($clean.Length -gt 2) {
    $score += 20
  }
  return $score
}

$inputPayload = Get-Content -Raw -Encoding UTF8 $InputJson | ConvertFrom-Json
$imagePath = [string]$inputPayload.image_path
$debugDir = $inputPayload.debug_artifacts_dir
$source = [System.Drawing.Bitmap]::FromFile($imagePath)
$fields = @{}
$warnings = New-Object System.Collections.Generic.List[string]
$warnings.Add("ocr_confidence_unavailable")
$tempFiles = New-Object System.Collections.Generic.List[string]
$preprocessingVariants = GetPreprocessingVariants $inputPayload

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

    if (-not (RegionHasInk $source $x $y $w $h)) {
      $fields[$fieldName] = @{
        raw_text = ""
        confidence = $null
        confidence_source = "unavailable"
        bounding_box = @{
          x = "0"
          y = "0"
          width = [string][decimal]$w
          height = [string][decimal]$h
        }
        lines = @()
        warnings = @("ocr_confidence_unavailable", "preprocessing_pipeline:blank_roi_fast_path")
      }
      continue
    }

    $best = $null
    $bestScore = -9999
    foreach ($variant in $preprocessingVariants) {
      $crop = NewCropBitmap $source $x $y $w $h $variant
      $variantName = [string]$variant.name
      if ([string]::IsNullOrWhiteSpace($variantName)) { $variantName = "unnamed" }
      if ($debugDir) {
        [System.IO.Directory]::CreateDirectory([string]$debugDir) | Out-Null
        $cropPath = Join-Path ([string]$debugDir) ($fieldName + "." + $variantName + ".png")
      } else {
        $cropPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString("N") + ".png")
        $tempFiles.Add($cropPath)
      }
      $crop.Save($cropPath, [System.Drawing.Imaging.ImageFormat]::Png)
      $cropWidth = $crop.Width
      $cropHeight = $crop.Height
      $crop.Dispose()
      $recognized = RecognizeImage $cropPath
      $score = ScoreRecognizedText $fieldName $recognized.text
      if ($score -gt $bestScore) {
        $bestScore = $score
        $best = @{
          recognized = $recognized
          pipeline_name = $variantName
          width = $cropWidth
          height = $cropHeight
        }
      }
    }
    if ($null -eq $best) {
      throw "No OCR preprocessing pipeline produced a result."
    }
    $fields[$fieldName] = @{
      raw_text = $best.recognized.text
      confidence = $null
      confidence_source = "unavailable"
      bounding_box = @{
        x = "0"
        y = "0"
        width = [string][decimal]($best.width)
        height = [string][decimal]($best.height)
      }
      lines = @($best.recognized.lines)
      warnings = @("ocr_confidence_unavailable", ("preprocessing_pipeline:" + $best.pipeline_name))
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
