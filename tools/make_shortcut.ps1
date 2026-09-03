# 重新生成带羽毛图标的启动快捷方式。
# .pyw 文件本身的图标由 Windows 的文件关联决定（显示为 Python 图标），改不了；
# 快捷方式则可以指定任意图标，所以用它作为日常入口。
$root = Split-Path -Parent $PSScriptRoot
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $root "CourseMate 刷课助手.lnk"))
$lnk.TargetPath = (Get-Command pythonw).Source
$lnk.Arguments = '"' + (Join-Path $root "CourseMate.pyw") + '"'
$lnk.WorkingDirectory = $root
$lnk.IconLocation = (Join-Path $root "assets\app.ico") + ",0"
$lnk.Description = "CourseMate 刷课助手"
$lnk.Save()
Write-Output "快捷方式已生成: $root\CourseMate 刷课助手.lnk"
