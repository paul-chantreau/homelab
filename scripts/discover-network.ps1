# Read-only Windows equivalent of scripts/discover-network.sh.
# IP configuration does not reveal Netgear VLAN membership or switch port mapping.
$ErrorActionPreference = "Stop"

$adapters = Get-NetAdapter | Select-Object Name, MacAddress, Status, LinkSpeed
$addresses = Get-NetIPConfiguration | Select-Object InterfaceAlias, IPv4Address, IPv4DefaultGateway, DNSServer
$routes = Get-NetRoute -AddressFamily IPv4 | Select-Object DestinationPrefix, NextHop, InterfaceAlias
$dns = Get-DnsClientServerAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, ServerAddresses

$report = [ordered]@{
    source = "read-only-windows-commands"
    limitation = "This report is local IP configuration only. It does not identify Netgear VLAN membership, PVID values, or physical switch ports."
    adapters = @($adapters)
    addresses = @($addresses)
    routes = @($routes)
    dns = @($dns)
}

$destination = if ($args.Count -ge 1) { $args[0] } else { Join-Path (Get-Location) "network-discovery.json" }
$report | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 -Path $destination
Write-Output "Wrote $destination"
Write-Output $report.limitation
$adapters | Format-Table -AutoSize | Out-String | Write-Output
