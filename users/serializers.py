from rest_framework import serializers
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    """Serializer for user registration"""
    password = serializers.CharField(write_only=True, required=True)
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ('email', 'nombre', 'apellido', 'telefono', 'ciudad', 'password', 'password2', 'rol')

    def validate(self, data):
        if data['password'] != data['password2']:
            raise serializers.ValidationError({'password': 'Las contraseñas no coinciden'})
        return data

    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(**validated_data)
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user profile information"""
    password = serializers.CharField(write_only=True, required=False)
    password2 = serializers.CharField(write_only=True, required=False)
    full_name = serializers.CharField(source='get_full_name', read_only=True)

    class Meta:
        model = User
        fields = ('id', 'email', 'nombre', 'apellido', 'full_name', 'telefono', 'ciudad', 
                  'foto_perfil', 'rol', 'is_active', 'date_joined', 'password', 'password2')
        read_only_fields = ('id', 'email', 'is_active', 'date_joined', 'full_name', 'rol')

    def validate(self, data):
        if 'password' in data and 'password2' in data:
            if data['password'] != data['password2']:
                raise serializers.ValidationError({'password': 'Las contraseñas no coinciden'})
        return data

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        validated_data.pop('password2', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        if password:
            instance.set_password(password)
        
        instance.save()
        return instance


class LoginSerializer(serializers.Serializer):
    """Serializer for user login"""
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            raise serializers.ValidationError('Email y contraseña son requeridos')

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError('Email o contraseña incorrectos')

        if not user.check_password(password):
            raise serializers.ValidationError('Email o contraseña incorrectos')

        if not user.is_active:
            raise serializers.ValidationError('La cuenta está desactivada')

        data['user'] = user
        return data
