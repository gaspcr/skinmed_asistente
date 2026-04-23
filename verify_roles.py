"""
Script de verificación: Sistema de roles dinámico
Verifica que todos los workflows estén correctamente registrados
"""
import sys
import os

# Asegurar que el directorio raiz está en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importar workflows para activar decoradores
from app.workflows import doctor, manager, hybrid
from app.workflows.llm.config import get_registered_llm_roles, get_llm_config
from app.workflows.role_registry import (
    get_registered_roles, 
    get_workflow_handler,
    is_role_registered
)

def test_role_registry():
    print("=" * 60)
    print("🧪 VERIFICACIÓN DEL SISTEMA DE ROLES DINÁMICO")
    print("=" * 60)
    print()
    
    # Test 1: Verificar roles registrados
    print("📋 Test 1: Roles Registrados")
    print("-" * 60)
    registered_roles = get_registered_roles()
    print(f"Roles encontrados: {registered_roles}")
    print(f"Total: {len(registered_roles)} roles")
    print()
    
    expected_roles = ['medico', 'gerencia', 'medico_gerencia']
    missing_roles = [r for r in expected_roles if r not in registered_roles]
    
    if missing_roles:
        print(f"❌ FALTA: Roles esperados no encontrados: {missing_roles}")
        return False
    else:
        print(f"✅ PASS: Todos los roles esperados están registrados")
    print()
    
    # Test 2: Verificar que cada rol tiene un handler
    print("🔍 Test 2: Obtener Handlers")
    print("-" * 60)
    all_handlers_ok = True
    
    for role in expected_roles:
        handler = get_workflow_handler(role)
        if handler:
            print(f"✅ {role:20} -> {handler.__class__.__name__}")
        else:
            print(f"❌ {role:20} -> NO HANDLER")
            all_handlers_ok = False
    print()
    
    if not all_handlers_ok:
        print("❌ FALLA: Algunos roles no tienen handler")
        return False
    else:
        print("✅ PASS: Todos los roles tienen handler válido")
    print()
    
    # Test 3: Verificar validación de roles
    print("🎯 Test 3: Validación de Roles")
    print("-" * 60)
    
    # Rol válido
    if is_role_registered("medico"):
        print("✅ 'medico' está registrado")
    else:
        print("❌ 'medico' NO está registrado")
        return False
    
    # Rol inválido
    if not is_role_registered("rol_inexistente"):
        print("✅ 'rol_inexistente' correctamente NO registrado")
    else:
        print("❌ 'rol_inexistente' incorrectamente registrado")
        return False
    
    # Normalización (mayúsculas/minúsculas)
    if is_role_registered("MEDICO"):
        print("✅ Normalización funciona ('MEDICO' -> 'medico')")
    else:
        print("❌ Normalización falló")
        return False
    print()
    
    # Test 4: Verificar que handlers con rol inexistente retornan None
    print("🚫 Test 4: Roles No Existentes")
    print("-" * 60)
    invalid_handler = get_workflow_handler("rol_inventado")
    if invalid_handler is None:
        print("✅ get_workflow_handler() retorna None para roles inexistentes")
    else:
        print("❌ get_workflow_handler() debería retornar None para roles inexistentes")
        return False
    print()
    
    # Test 5: Verificar configuraciones LLM
    print("🤖 Test 5: Configuraciones LLM")
    print("-" * 60)
    llm_roles = get_registered_llm_roles()
    print(f"Roles con config LLM: {llm_roles}")
    
    expected_llm_roles = ['medico', 'gerencia']
    missing_llm = [r for r in expected_llm_roles if r not in llm_roles]
    if missing_llm:
        print(f"❌ FALTA: Configs LLM esperadas no encontradas: {missing_llm}")
        return False
    
    for role in llm_roles:
        cfg = get_llm_config(role)
        tools = [t['function']['name'] for t in cfg.tools]
        print(f"✅ {role:20} -> {len(cfg.tools)} tools: {tools}")
    print()
    
    print("=" * 60)
    print("🎉 TODAS LAS VERIFICACIONES PASARON")
    print("=" * 60)
    print()
    print("📝 Resumen:")
    print(f"   - {len(registered_roles)} roles registrados correctamente")
    print(f"   - {len(llm_roles)} configuraciones LLM registradas")
    print(f"   - Todos los handlers funcionan")
    print(f"   - Validación de roles funciona")
    print(f"   - Normalización funciona correctamente")
    print()
    return True

if __name__ == "__main__":
    try:
        success = test_role_registry()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR DURANTE LA VERIFICACIÓN: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
