#region rgate wrapper (relay-gate + sigil one-liner)
# Usage:
#   rgate doctor
#   rgate channels list
#   rgate channels get --id 40
#   rgate channels update --id 40 --set-models "a,b,c" --apply
#   rgate channels status --id 41 --status 1 --apply
#   rgate channels test --id 40 --model "deepseek-ai/deepseek-v4-flash"
#   rgate channels update --input '{"id":40,"fields":{"models":"..."}}' --apply  # full JSON still works
function rgate {
    $env:SIGIL_PASSPHRASE = [Environment]::GetEnvironmentVariable('SIGIL_PASSPHRASE','User')
    $exe = "D:\AgentWork\state\relay-gate\bin\relay-gate.exe"
    sigil exec SIGIL_ADMIN_TOKEN --apply -- $exe @args
}
#endregion
