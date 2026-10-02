$root = "d:\AIProjects\AutoTeams\backend\sample_data\example-enterprise"

Write-Host "=== 1. Customer ID consistency ==="
$customers = Import-Csv "$root\05-crm\customers.csv" | Select-Object -ExpandProperty customer_id
$contactsCust = Import-Csv "$root\05-crm\contacts.csv" | Select-Object -ExpandProperty customer_id | Sort-Object -Unique
$oppsCust = Import-Csv "$root\05-crm\opportunities.csv" | Select-Object -ExpandProperty customer_id | Sort-Object -Unique
$actsCust = Import-Csv "$root\05-crm\activities.csv" | Select-Object -ExpandProperty customer_id | Sort-Object -Unique
$ordersCust = Import-Csv "$root\06-orders\orders.csv" | Select-Object -ExpandProperty customer_id | Sort-Object -Unique
$allCustRefs = $contactsCust + $oppsCust + $actsCust + $ordersCust | Sort-Object -Unique
$missingCust = $allCustRefs | Where-Object { $_ -notin $customers }
if ($missingCust) { Write-Host "FAIL: Missing customer IDs: $($missingCust -join ',')" } else { Write-Host "PASS: All customer ID references valid" }

Write-Host ""
Write-Host "=== 2. Owner/employee ID consistency ==="
$orgIds = @("SL-2018-001","SL-2018-002","SL-2018-003","SL-2018-004","SL-2018-005","SL-2018-006","SL-2018-007","SL-2019-015","SL-2019-020","SL-2019-025","SL-2020-032","SL-2020-035","SL-2020-038","SL-2020-040","SL-2020-042","SL-2021-045","SL-2021-048","SL-2021-050","SL-2021-052","SL-2022-058","SL-2022-060","SL-2022-062","SL-2022-065","SL-2023-075")
$crmOwners = Import-Csv "$root\05-crm\customers.csv" | Select-Object -ExpandProperty owner | Sort-Object -Unique
$oppOwners = Import-Csv "$root\05-crm\opportunities.csv" | Select-Object -ExpandProperty owner | Sort-Object -Unique
$actOwners = Import-Csv "$root\05-crm\activities.csv" | Select-Object -ExpandProperty owner | Sort-Object -Unique
$orderSales = Import-Csv "$root\06-orders\orders.csv" | Select-Object -ExpandProperty sales_person | Sort-Object -Unique
$allOwners = $crmOwners + $oppOwners + $actOwners + $orderSales | Sort-Object -Unique
$missingOwners = $allOwners | Where-Object { $_ -notin $orgIds }
if ($missingOwners) { Write-Host "FAIL: Missing employee IDs: $($missingOwners -join ',')" } else { Write-Host "PASS: All owner/sales_person IDs exist in org structure" }

Write-Host ""
Write-Host "=== 3. Opportunity stages (need >=1 quoting) ==="
$opps = Import-Csv "$root\05-crm\opportunities.csv"
$quotingOpps = $opps | Where-Object { $_.stage -eq "报价中" }
Write-Host "Quoting opportunities: $($quotingOpps.Count)"
$approvalOpps = $opps | Where-Object { $_.stage -eq "审批中" }
Write-Host "Approval-in-progress opportunities: $($approvalOpps.Count)"
if ($quotingOpps.Count -ge 1) { Write-Host "PASS: At least 1 quoting opportunity" } else { Write-Host "FAIL: No quoting opportunity" }

Write-Host ""
Write-Host "=== 4. Order product_id in price list ==="
$orderProducts = Import-Csv "$root\06-orders\orders.csv" | Select-Object -ExpandProperty product_id | Sort-Object -Unique
$priceListIds = @("SL-T100-RTU","SL-T100-MQTT","SL-T100-NBIOT","SL-P200-A","SL-P200-B","SL-P200-C","SL-G300-1","SL-G300-2","SL-G300-4","SL-G300-EX","SL-GW500-STD","SL-GW500-LORA","SL-GW500-DUAL","SL-GW500-UPS","SL-GW510-STD","SL-GW510-POE","SL-GW510-OUT","SL-CT800-STD","SL-CT800-AI","SL-CT800-SIL2","SL-DC600-STD","SL-DC600-HSC","SL-DC600-UPS")
$missingProducts = $orderProducts | Where-Object { $_ -notin $priceListIds }
if ($missingProducts) { Write-Host "FAIL: Product IDs not in price list: $($missingProducts -join ',')" } else { Write-Host "PASS: All order product IDs exist in price list" }

Write-Host ""
Write-Host "=== 5. Phone format ==="
$custPhones = Import-Csv "$root\05-crm\customers.csv" | Select-Object -ExpandProperty phone
$badPhones = $custPhones | Where-Object { $_ -notmatch '^1\d{2}-\d{4}-\d{4}$' }
if ($badPhones) { Write-Host "FAIL: Bad phone format: $($badPhones -join ',')" } else { Write-Host "PASS: Customer phone format correct" }

Write-Host ""
Write-Host "=== 6. Email format ==="
$custEmails = Import-Csv "$root\05-crm\customers.csv" | Select-Object -ExpandProperty email
$badEmails = $custEmails | Where-Object { $_ -notmatch '^[^@]+@[^@]+\.[^@]+$' }
if ($badEmails) { Write-Host "FAIL: Bad email format: $($badEmails -join ',')" } else { Write-Host "PASS: Customer email format correct" }

Write-Host ""
Write-Host "=== 7. Contact associations ==="
$contacts = Import-Csv "$root\05-crm\contacts.csv"
$missingContactCust = $contacts | Where-Object { $_.customer_id -notin $customers }
if ($missingContactCust) { Write-Host "FAIL: Contact references missing customer" } else { Write-Host "PASS: All contacts reference valid customers" }
Write-Host "Total contacts: $($contacts.Count)"

Write-Host ""
Write-Host "=== 8. Activity associations ==="
$acts = Import-Csv "$root\05-crm\activities.csv"
$oppIds = $opps | Select-Object -ExpandProperty opp_id
$actsWithOpp = $acts | Where-Object { $_.opp_id -ne "" }
$missingActOpp = $actsWithOpp | Where-Object { $_.opp_id -notin $oppIds }
if ($missingActOpp) { Write-Host "FAIL: Activity references missing opportunity" } else { Write-Host "PASS: All activities reference valid opportunities" }
Write-Host "Total activities: $($acts.Count)"

Write-Host ""
Write-Host "=== 9. Order associations ==="
$orders = Import-Csv "$root\06-orders\orders.csv"
$missingOrderCust = $orders | Where-Object { $_.customer_id -notin $customers }
if ($missingOrderCust) { Write-Host "FAIL: Order references missing customer" } else { Write-Host "PASS: All orders reference valid customers" }
Write-Host "Total orders: $($orders.Count)"
$completedOrders = $orders | Where-Object { $_.status -eq "已完成" }
Write-Host "Completed orders: $($completedOrders.Count)"
$feedbackOrders = $completedOrders | Where-Object { $_.notes -match "feedback|客户反馈" }
Write-Host "Orders with feedback: $($feedbackOrders.Count)"

Write-Host ""
Write-Host "=== 10. File statistics ==="
$allFiles = Get-ChildItem -Path $root -Recurse -File | Where-Object { $_.Name -ne "validate.ps1" }
Write-Host "Total files: $($allFiles.Count)"
$mdFiles = $allFiles | Where-Object { $_.Extension -eq ".md" }
$csvFiles = $allFiles | Where-Object { $_.Extension -eq ".csv" }
Write-Host "Markdown files: $($mdFiles.Count)"
Write-Host "CSV files: $($csvFiles.Count)"

Write-Host ""
Write-Host "=== 11. Customer distribution ==="
$custData = Import-Csv "$root\05-crm\customers.csv"
$sLevel = ($custData | Where-Object { $_.customer_level -eq "S" }).Count
$aLevel = ($custData | Where-Object { $_.customer_level -eq "A" }).Count
$bLevel = ($custData | Where-Object { $_.customer_level -eq "B" }).Count
$cLevel = ($custData | Where-Object { $_.customer_level -eq "C" }).Count
Write-Host "S-level: $sLevel, A-level: $aLevel, B-level: $bLevel, C-level: $cLevel (target: 3/5/7/5)"
