// Registers the widget provider with COM, then waits until no Tavinor
// widget is pinned anywhere (the widget host starts us when it needs us).
using System.Runtime.InteropServices;
using COM;
using TavinorWidget;

[DllImport("ole32.dll")]
static extern int CoRegisterClassObject([MarshalAs(UnmanagedType.LPStruct)] Guid rclsid,
    [MarshalAs(UnmanagedType.IUnknown)] object pUnk, uint dwClsContext, uint flags, out uint lpdwRegister);
[DllImport("ole32.dll")] static extern int CoRevokeClassObject(uint dwRegister);

// Must match the com:Class Id and CreateInstance ClassId in the package manifest.
var clsid = Guid.Parse(WidgetProvider.ClassId);
CoRegisterClassObject(clsid, new WidgetProviderFactory<WidgetProvider>(), 0x4 /* LOCAL_SERVER */,
                      0x1 /* MULTIPLEUSE */, out uint cookie);
WidgetProvider.NoWidgetsLeft.WaitOne();
CoRevokeClassObject(cookie);
