// The class factory COM asks for when the widget host wants our provider.
// Standard boilerplate, from Microsoft's "Implement a widget provider in a
// C# Windows App" guide.
using Microsoft.Windows.Widgets.Providers;
using System.Runtime.InteropServices;
using WinRT;

namespace COM
{
    static class Guids
    {
        public const string IClassFactory = "00000001-0000-0000-C000-000000000046";
        public const string IUnknown = "00000000-0000-0000-C000-000000000046";
    }

    [ComImport, ComVisible(false), InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid(Guids.IClassFactory)]
    internal interface IClassFactory
    {
        [PreserveSig]
        int CreateInstance(IntPtr pUnkOuter, ref Guid riid, out IntPtr ppvObject);
        [PreserveSig]
        int LockServer(bool fLock);
    }

    [ComVisible(true)]
    class WidgetProviderFactory<T> : IClassFactory where T : IWidgetProvider, new()
    {
        public int CreateInstance(IntPtr pUnkOuter, ref Guid riid, out IntPtr ppvObject)
        {
            ppvObject = IntPtr.Zero;
            if (pUnkOuter != IntPtr.Zero)
                Marshal.ThrowExceptionForHR(CLASS_E_NOAGGREGATION);
            if (riid == typeof(T).GUID || riid == Guid.Parse(Guids.IUnknown))
                ppvObject = MarshalInspectable<IWidgetProvider>.FromManaged(new T());
            else
                Marshal.ThrowExceptionForHR(E_NOINTERFACE);
            return 0;
        }

        int IClassFactory.LockServer(bool fLock) => 0;

        private const int CLASS_E_NOAGGREGATION = -2147221232;
        private const int E_NOINTERFACE = -2147467262;
    }
}
