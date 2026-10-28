param([switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$settings = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = 'До встречи — подключение GigaChat'
$form.ClientSize = New-Object System.Drawing.Size(660, 340)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.AutoScaleMode = 'Font'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)

$heading = New-Object System.Windows.Forms.Label
$heading.Text = 'Вставьте Authorization Key из настроек API GigaChat'
$heading.SetBounds(24, 20, 612, 48)
$form.Controls.Add($heading)

$keyBox = New-Object System.Windows.Forms.TextBox
$keyBox.SetBounds(24, 78, 612, 30)
$keyBox.UseSystemPasswordChar = $true
$keyBox.ShortcutsEnabled = $true
$keyBox.MaxLength = 4096
$form.Controls.Add($keyBox)

$count = New-Object System.Windows.Forms.Label
$count.Text = 'Поле пустое. Нажмите Ctrl+V, чтобы вставить ключ.'
$count.SetBounds(24, 116, 612, 26)
$form.Controls.Add($count)

$notice = New-Object System.Windows.Forms.Label
$notice.SetBounds(24, 151, 612, 74)
if ($settings.message) {
    $notice.Text = [string]$settings.message
    $notice.ForeColor = [System.Drawing.Color]::Firebrick
} else {
    $notice.Text = 'Ключ скрыт точками и сохраняется только в локальный файл .env. Здесь подключение к API ещё не проверяется.'
}
$form.Controls.Add($notice)

$save = New-Object System.Windows.Forms.Button
$save.Text = 'Сохранить'
$save.SetBounds(370, 258, 126, 38)
$save.Enabled = $false
$form.Controls.Add($save)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Отмена'
$cancel.SetBounds(510, 258, 126, 38)
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)
$form.AcceptButton = $save
$form.CancelButton = $cancel

$keyBox.Add_TextChanged({
    $length = $keyBox.Text.Trim().Length
    $save.Enabled = $length -gt 0
    $count.Text = if ($length -gt 0) { "Вставлено символов: $length" } else { 'Поле пустое. Нажмите Ctrl+V, чтобы вставить ключ.' }
})
$save.Add_Click({
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})
$form.Add_Shown({ $form.Activate(); [void]$keyBox.Focus() })

try {
    if ($VerifyOnly) {
        $keyBox.Text = 'test-only'
        [Console]::Write((@{status = 'verified'; masked = $keyBox.UseSystemPasswordChar; save_enabled = $save.Enabled; count = $count.Text; visible = $form.Visible} | ConvertTo-Json -Compress))
    } elseif ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Write((@{status = 'submitted'; key = $keyBox.Text} | ConvertTo-Json -Compress))
    } else {
        [Console]::Write('{"status":"cancelled"}')
    }
} finally {
    $keyBox.Clear()
    $form.Dispose()
}
